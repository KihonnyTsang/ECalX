"""Frozen virtual oracle + resumable, transaction-per-round integration loop."""
import json
import random
from pathlib import Path

from .adapters import build_environment
from .contracts import (CreateVirtualInput, ExperimentFeedback, ExperimentError,
                        LoopInput, LoopOutput, QueryVirtualInput, VirtualExperimentOutput)
from .storage import Store, dump, digest, identifier
from .surrogate import fit_observed, feedback_digest
from .optimization import OptimizationMixin


class VirtualLaboratory(OptimizationMixin, Store):
    def __init__(self, storage_root, project_root):
        super().__init__(Path(storage_root)/'virtual/experiments.sqlite3')
        self.project_root = Path(project_root)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS environments(id TEXT PRIMARY KEY, public TEXT, hidden TEXT, seal TEXT);
                CREATE TABLE IF NOT EXISTS observations(experiment_id TEXT, candidate_id TEXT, query_id TEXT,
                    feedback TEXT, PRIMARY KEY(experiment_id,candidate_id), UNIQUE(experiment_id,query_id));
                CREATE TABLE IF NOT EXISTS query_requests(experiment_id TEXT, request_id TEXT, candidate_id TEXT,
                    PRIMARY KEY(experiment_id,request_id));
                INSERT OR IGNORE INTO query_requests SELECT experiment_id,query_id,candidate_id FROM observations;
                CREATE TABLE IF NOT EXISTS models(experiment_id TEXT, version INTEGER, payload TEXT,
                    PRIMARY KEY(experiment_id,version));
                CREATE TABLE IF NOT EXISTS rounds(experiment_id TEXT, round INTEGER, payload TEXT,
                    PRIMARY KEY(experiment_id,round));
                CREATE TRIGGER IF NOT EXISTS freeze_environment BEFORE UPDATE ON environments
                    BEGIN SELECT RAISE(ABORT, 'frozen environment'); END;
                CREATE TRIGGER IF NOT EXISTS freeze_model BEFORE UPDATE ON models
                    BEGIN SELECT RAISE(ABORT, 'immutable model version'); END;
            ''')
        self._init_optimization()

    def create_virtual_experiment(self, request: CreateVirtualInput, *, operation_key=None) -> VirtualExperimentOutput:
        # Reject before reading a data source if no scoped human grant exists.
        with self.connect() as db:
            self.require(db, request.authorization_id, 'CREATE_VIRTUAL', 'new')
            fingerprint = digest(request.model_dump(exclude={'authorization_id'}))
            cached = self.receipt(db, operation_key, fingerprint)
            if cached is not None:
                return VirtualExperimentOutput(**cached)
            eid = identifier('virtual')
            try:
                candidates, hidden, conditions = build_environment(self.project_root, request)
            except ArithmeticError as error:
                raise ExperimentError('模拟数值超出浮点计算范围；请调整明确实验条件') from error
            environment_sha = digest({'candidates': candidates, 'hidden': hidden, 'conditions': conditions})
            renamed = {}
            for c in candidates:
                old = c['candidate_id']
                c['candidate_id'] = eid + ':' + old
                renamed[c['candidate_id']] = hidden[old]
            initial_ids = [candidates[round(i*(len(candidates)-1)/(request.initial_count-1))]['candidate_id']
                           for i in range(request.initial_count)]
            public = {'experiment_id': eid, 'adapter': request.adapter, 'seed': request.seed,
                      'candidates': candidates, 'conditions': conditions, 'initial_ids': initial_ids,
                      'code_version': 'm4e2-1', 'environment_sha256': environment_sha,
                      'creation_parameters': request.model_dump(exclude={'authorization_id'})}
            seal = digest({'public': public, 'hidden': renamed})
            db.execute('INSERT INTO environments VALUES(?,?,?,?)', (eid, dump(public), dump(renamed), seal))
            self.event(db, 'CREATE_VIRTUAL', eid, {'adapter': request.adapter, 'seed': request.seed,
                                                 'frozen_sha256': seal, 'grant': request.authorization_id})
            for cid in initial_ids:
                self._query(db, eid, cid, 'initial:'+cid, 'CREATE_VIRTUAL_INITIAL')
            self._fit(db, eid)
            output = self._public(db, eid)
            self.save_receipt(db, operation_key, fingerprint, output.model_dump())
            return output

    def _environment(self, db, eid):
        row = db.execute('SELECT * FROM environments WHERE id=?', (eid,)).fetchone()
        if row is None:
            raise ExperimentError('未知虚拟实验ID')
        public, hidden = json.loads(row['public']), json.loads(row['hidden'])
        if digest({'public': public, 'hidden': hidden}) != row['seal']:
            raise ExperimentError('冻结环境完整性校验失败')
        return public, hidden, row['seal']

    def _observations(self, db, eid):
        return [json.loads(r['feedback']) for r in db.execute(
            'SELECT feedback FROM observations WHERE experiment_id=? ORDER BY rowid', (eid,))]

    def _public_environment(self, db, eid):
        # This path cannot deserialize hidden labels. Selection and fitting use it.
        row = db.execute('SELECT public,seal FROM environments WHERE id=?', (eid,)).fetchone()
        if row is None:
            raise ExperimentError('未知虚拟实验ID')
        return json.loads(row['public']), row['seal']

    def _public(self, db, eid):
        public, seal = self._public_environment(db, eid)
        initial = set(public['initial_ids'])
        return VirtualExperimentOutput(**{k: public[k] for k in (
            'experiment_id', 'adapter', 'seed', 'candidates', 'conditions')}, frozen_sha256=seal,
            initial_feedback=[r for r in self._observations(db, eid) if r['candidate_id'] in initial])

    def get(self, eid) -> VirtualExperimentOutput:
        with self.connect() as db:
            return self._public(db, eid)

    def list_experiments(self):
        with self.connect() as db:
            return [self._public(db, r['id']) for r in db.execute('SELECT id FROM environments ORDER BY rowid DESC')]

    def _query(self, db, eid, cid, qid, reason):
        public, hidden, _ = self._environment(db, eid)
        if cid not in hidden:
            raise ExperimentError('候选不在冻结池中；未测新材料不能获得历史回放值')
        previous = db.execute('SELECT * FROM query_requests WHERE experiment_id=? AND request_id=?', (eid, qid)).fetchone()
        if previous and previous['candidate_id'] != cid:
            raise ExperimentError('同一请求ID不能指向不同候选')
        existing = db.execute('SELECT feedback FROM observations WHERE experiment_id=? AND candidate_id=?', (eid, cid)).fetchone()
        db.execute('INSERT OR IGNORE INTO query_requests VALUES(?,?,?)', (eid, qid, cid))
        if existing:
            feedback = json.loads(existing['feedback'])
            self.event(db, 'QUERY_CACHED', eid, {'candidate_id': cid, 'request_id': qid, 'original_query_id': feedback['query_id']})
            return ExperimentFeedback(**feedback)
        label = hidden[cid]
        result = ExperimentFeedback(candidate_id=cid, value=label['value'], target=public['conditions']['target'],
                                    provenance=public['adapter'], source=label['source'], seed=public['seed'], query_id=qid)
        db.execute('INSERT INTO observations VALUES(?,?,?,?)', (eid, cid, qid, result.model_dump_json()))
        self.event(db, 'QUERY_VIRTUAL', eid, {'reason': reason, 'feedback': result.model_dump(), 'conditions': public['conditions']})
        return result

    def query_virtual_experiment(self, request: QueryVirtualInput) -> ExperimentFeedback:
        with self.connect() as db:
            self.require(db, request.authorization_id, 'QUERY_VIRTUAL', request.experiment_id)
            if self._plan(db, request.experiment_id) and not db.execute(
                    'SELECT 1 FROM observations WHERE experiment_id=? AND candidate_id=?',
                    (request.experiment_id, request.candidate_id)).fetchone():
                raise ExperimentError('已冻结优化预算；新查询必须经策略执行器计费')
            return self._query(db, request.experiment_id, request.candidate_id, request.request_id, 'EXPLICIT_QUERY')

    def _fit(self, db, eid):
        public, _ = self._public_environment(db, eid)
        # Only observations cross the model boundary, never `hidden`.
        payload = fit_observed(public['candidates'], self._observations(db, eid), public['conditions'])
        version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM models WHERE experiment_id=?', (eid,)).fetchone()[0]
        payload['model_version'] = f'{eid}:gp-v{version}'
        db.execute('INSERT INTO models VALUES(?,?,?)', (eid, version, dump(payload)))
        self.event(db, 'FIT_VIRTUAL_MODEL', eid, {'model_version': payload['model_version'],
                    'training_sha256': payload['training_sha256'], 'training_count': len(payload['training_candidate_ids']),
                    'provenance': payload['training_provenance'], 'contains_simulated_labels': payload['contains_simulated_labels']})
        return payload

    def run_minimal_loop(self, request: LoopInput) -> LoopOutput:
        with self.connect() as db:
            self.require(db, request.authorization_id, 'RUN_VIRTUAL_LOOP', request.experiment_id)
            if self._plan(db, request.experiment_id):
                raise ExperimentError('E2任务请使用已冻结预算执行器')
            public, _ = self._public_environment(db, request.experiment_id)
            if request.rounds > len(public['candidates'])-len(self._observations(db, request.experiment_id)):
                raise ExperimentError('预算超过剩余未查询候选；不会静默重复查询或缩减预算')
        for _ in range(request.rounds):
            with self.connect() as db:
                self.require(db, request.authorization_id, 'RUN_VIRTUAL_LOOP', request.experiment_id)
                eid = request.experiment_id
                public, _ = self._public_environment(db, eid)
                observed = self._observations(db, eid)
                before = json.loads(db.execute('SELECT payload FROM models WHERE experiment_id=? ORDER BY version DESC LIMIT 1', (eid,)).fetchone()[0])
                # Manually queried data also participates before the next selection.
                if before['training_sha256'] != feedback_digest(observed):
                    before = self._fit(db, eid)
                observed_ids = {r['candidate_id'] for r in observed}
                remaining = [c for c in public['candidates'] if c['candidate_id'] not in observed_ids]
                if not remaining:
                    raise ExperimentError('冻结池已耗尽')
                n = db.execute('SELECT COUNT(*) FROM rounds WHERE experiment_id=?', (eid,)).fetchone()[0]+1
                selected = random.Random(f"{public['seed']}:{n}").choice(remaining)
                feedback = self._query(db, eid, selected['candidate_id'], f'loop-round-{n}', 'FIXED_SEED_RANDOM_INTEGRATION')
                after = self._fit(db, eid)  # Genuine refit with newly revealed feedback every round.
                trace = {'round': n, 'strategy': 'Random Sampling (E1 integration only)',
                         'candidate_id': selected['candidate_id'], 'formula': selected['formula'],
                         'reason': 'fixed seed random draw from unqueried frozen pool; no optimization advantage claim',
                         'prediction_before': before['predictions'][selected['candidate_id']],
                         'predictions_before': before['predictions'], 'feedback': feedback.model_dump(),
                         'model_before': before['model_version'], 'model_after': after['model_version'],
                         'training_ids_after': after['training_candidate_ids'], 'training_sha256_after': after['training_sha256'],
                         'cumulative_best': max(r['value'] for r in self._observations(db, eid)),
                         'observations': len(after['training_candidate_ids']), 'provenance': public['adapter']}
                db.execute('INSERT INTO rounds VALUES(?,?,?)', (eid, n, dump(trace)))
                self.event(db, 'COMPLETE_ROUND', eid, trace)
        return self.report(request.experiment_id)

    def report(self, eid) -> LoopOutput:
        with self.connect() as db:
            self._public_environment(db, eid)
            trace = [json.loads(r[0]) for r in db.execute('SELECT payload FROM rounds WHERE experiment_id=? ORDER BY round', (eid,))]
            latest = db.execute('SELECT payload FROM models WHERE experiment_id=? ORDER BY version DESC LIMIT 1', (eid,)).fetchone()
            return LoopOutput(experiment_id=eid, completed_rounds=len(trace), observations=len(self._observations(db, eid)),
                              latest_model_version=json.loads(latest[0])['model_version'], trace=trace)

    def export_report(self, eid):
        with self.connect() as db:
            public = self._public(db, eid).model_dump()
            metadata, _ = self._public_environment(db, eid)
            observations = self._observations(db, eid)
            versions = [json.loads(r[0]) for r in db.execute('SELECT payload FROM models WHERE experiment_id=? ORDER BY version', (eid,))]
        # Reconstruct a restart request for immutable E1 environments too.
        conditions = public['conditions']
        creation = metadata.get('creation_parameters') or {
            'adapter': public['adapter'], 'seed': public['seed'],
            'initial_count': len(public['initial_feedback']),
            **{k: conditions[k] for k in ('temperature_K', 'e_start_kV_cm', 'e_end_kV_cm')},
            'candidate_keys': [c['candidate_id'].rsplit(':', 1)[-1] for c in public['candidates']]}
        return {'schema_version': 'm4e2-1', 'experiment': public, 'observations': observations,
                'environment_sha256': metadata.get('environment_sha256', public['frozen_sha256']),
                'creation_parameters': creation,
                'optimization': self.optimization_state(eid),
                'models': versions, 'loop': self.report(eid).model_dump(),
                'audit': [r for r in self.audit() if r['subject'] == eid],
                'LLM_agent_enabled': False, 'M5_enabled': False,
                'notice': 'SIMULATED/已曝光HISTORICAL_REPLAY研究演示；非独立验证或真实材料发现；隐藏未查询标签不导出'}
