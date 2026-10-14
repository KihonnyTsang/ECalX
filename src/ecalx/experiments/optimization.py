"""Persisted budget and strategy execution. Each round and request progress commit together."""
import json

from .contracts import (AdvanceOptimizationInput, ConfigureOptimizationInput, ControlOptimizationInput,
                        ExperimentError, StrategyParameters)
from .storage import dump
from .strategies import SelectionInput, select_next_candidate
from .surrogate import feedback_digest


class OptimizationMixin:
    def _init_optimization(self):
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS optimization_plans(experiment_id TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS optimization_batches(experiment_id TEXT, request_id TEXT,
                    requested INTEGER, completed INTEGER, PRIMARY KEY(experiment_id,request_id));
            ''')

    def _plan(self, db, eid):
        row = db.execute('SELECT payload FROM optimization_plans WHERE experiment_id=?', (eid,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_plan(self, db, eid, plan):
        db.execute('UPDATE optimization_plans SET payload=? WHERE experiment_id=?', (dump(plan), eid))

    def optimization_state(self, eid):
        with self.connect() as db:
            self._public_environment(db, eid)
            return self._plan(db, eid)

    def configure_optimization(self, request: ConfigureOptimizationInput):
        with self.connect() as db:
            eid = request.experiment_id
            self.require(db, request.authorization_id, 'RUN_VIRTUAL_LOOP', eid)
            public, _ = self._public_environment(db, eid)
            plan = {'parameters': request.parameters.model_dump(), 'additional_budget': request.additional_budget,
                    'initial_cost': len(self._observations(db, eid)), 'query_unit_cost': 1,
                    'consumed': 0, 'status': 'READY'}
            existing = self._plan(db, eid)
            if existing:
                if any(existing[k] != plan[k] for k in ('parameters', 'additional_budget')):
                    raise ExperimentError('策略与预算已冻结；重启应新建实验，不覆盖旧任务')
                return existing
            if db.execute('SELECT COUNT(*) FROM rounds WHERE experiment_id=?', (eid,)).fetchone()[0]:
                raise ExperimentError('已有E1轨迹；请新建独立E2任务')
            if plan['initial_cost'] != len(public['initial_ids']):
                raise ExperimentError('已有额外手工查询；请新建固定初始集合的E2任务')
            if request.additional_budget > len(public['candidates'])-plan['initial_cost']:
                raise ExperimentError('预算超过未查询候选池；不重复计费或缩减预算')
            db.execute('INSERT INTO optimization_plans VALUES(?,?)', (eid, dump(plan)))
            self.event(db, 'CONFIGURE_OPTIMIZATION', eid, plan)
            return plan

    def control_optimization(self, request: ControlOptimizationInput):
        with self.connect() as db:
            eid = request.experiment_id
            self.require(db, request.authorization_id, 'RUN_VIRTUAL_LOOP', eid)
            plan = self._plan(db, eid)
            if plan is None:
                raise ExperimentError('请先冻结策略与预算')
            if plan['status'] == 'COMPLETED':
                raise ExperimentError('任务已完成；重启应新建实验')
            plan['status'] = 'PAUSED' if request.action == 'PAUSE' else 'READY'
            self._save_plan(db, eid, plan)
            self.event(db, request.action+'_OPTIMIZATION', eid, plan)
            return plan

    def optimization_request_progress(self, eid, request_id):
        with self.connect() as db:
            self._public_environment(db, eid)
            row = db.execute('SELECT requested,completed FROM optimization_batches WHERE experiment_id=? AND request_id=?',
                             (eid, request_id)).fetchone()
            return dict(row) if row else None

    def advance_optimization(self, request: AdvanceOptimizationInput, *, checkpoint=None):
        eid = request.experiment_id
        with self.connect() as db:
            self.require(db, request.authorization_id, 'RUN_VIRTUAL_LOOP', eid)
            plan = self._plan(db, eid)
            if plan is None:
                raise ExperimentError('请先冻结策略与预算')
            batch = db.execute('SELECT * FROM optimization_batches WHERE experiment_id=? AND request_id=?',
                               (eid, request.request_id)).fetchone()
            if batch and batch['requested'] != request.rounds:
                raise ExperimentError('重复请求ID不能改变轮数')
            outstanding = request.rounds - (batch['completed'] if batch else 0)
            if outstanding > plan['additional_budget']-plan['consumed']:
                raise ExperimentError('请求超出已冻结实验预算')
            if outstanding and plan['status'] == 'PAUSED':
                raise ExperimentError('任务已暂停；请显式恢复')
            db.execute('INSERT OR IGNORE INTO optimization_batches VALUES(?,?,?,0)',
                       (eid, request.request_id, request.rounds))
        while True:
            if checkpoint is not None:
                checkpoint()
            with self.connect() as db:
                self.require(db, request.authorization_id, 'RUN_VIRTUAL_LOOP', eid)
                batch = db.execute('SELECT * FROM optimization_batches WHERE experiment_id=? AND request_id=?',
                                   (eid, request.request_id)).fetchone()
                if batch['completed'] == batch['requested']:
                    break
                plan = self._plan(db, eid)
                if plan['status'] == 'PAUSED':
                    break  # A concurrent UI pause takes effect between atomic rounds.
                if plan['consumed'] >= plan['additional_budget']:
                    raise ExperimentError('已冻结实验预算耗尽')
                public, seal = self._public_environment(db, eid)
                observed = self._observations(db, eid)
                if len(observed) != plan['initial_cost']+plan['consumed']:
                    raise ExperimentError('非计划查询改变预算账目；拒绝继续')
                before = json.loads(db.execute('SELECT payload FROM models WHERE experiment_id=? ORDER BY version DESC LIMIT 1', (eid,)).fetchone()[0])
                if before['training_sha256'] != feedback_digest(observed) or 'posterior_std' not in before:
                    before = self._fit(db, eid)
                n = plan['consumed']+1
                selected = select_next_candidate(SelectionInput(candidates=public['candidates'], observations=observed,
                    predictions=before['predictions'], posterior_std=before['posterior_std'],
                    parameters=StrategyParameters(**plan['parameters']), seed=public['seed'], round=n))
                candidate = next(c for c in public['candidates'] if c['candidate_id'] == selected.candidate_id)
                # Oracle access starts only AFTER the selector has returned its choice.
                feedback = self._query(db, eid, selected.candidate_id, f'e2-round-{n}', 'M4E2_'+plan['parameters']['strategy'])
                after = self._fit(db, eid)
                error = before['predictions'][selected.candidate_id]-feedback.value
                trace = {'round': n, 'strategy': plan['parameters']['strategy'],
                    'strategy_parameters': plan['parameters'], 'candidate_id': selected.candidate_id,
                    'formula': candidate['formula'], 'reason': selected.reason,
                    'selection_scores': selected.scores, 'observed_best_before': selected.observed_best,
                    'prediction_before': before['predictions'][selected.candidate_id],
                    'posterior_std_before': before['posterior_std'][selected.candidate_id],
                    'predictions_before': before['predictions'], 'posterior_std_all_before': before['posterior_std'],
                    'feedback': feedback.model_dump(), 'prequential_error_K': error,
                    'prequential_absolute_error_K': abs(error),
                    'model_before': before['model_version'], 'model_after': after['model_version'],
                    'training_ids_before': before['training_candidate_ids'],
                    'training_ids_after': after['training_candidate_ids'],
                    'training_sha256_before': before['training_sha256'], 'training_sha256_after': after['training_sha256'],
                    'frozen_sha256': seal, 'environment_sha256': public.get('environment_sha256', seal),
                    'cumulative_best': max(r['value'] for r in self._observations(db, eid)),
                    'observations': len(after['training_candidate_ids']), 'provenance': public['adapter'],
                    'query_budget': {'unit_cost': 1, 'initial_cost': plan['initial_cost'],
                        'additional_consumed': n, 'additional_limit': plan['additional_budget'],
                        'total_consumed': plan['initial_cost']+n}}
                db.execute('INSERT INTO rounds VALUES(?,?,?)', (eid, n, dump(trace)))
                plan['consumed'] = n
                plan['status'] = 'COMPLETED' if n == plan['additional_budget'] else 'RUNNING'
                self._save_plan(db, eid, plan)
                db.execute('UPDATE optimization_batches SET completed=completed+1 WHERE experiment_id=? AND request_id=?',
                           (eid, request.request_id))
                self.event(db, 'COMPLETE_ROUND', eid, trace)
        return self.report(eid)
