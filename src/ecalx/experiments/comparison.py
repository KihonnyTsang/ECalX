"""Offline fair comparison runner. No LLM, real data store or production model."""
import hashlib
import json
from pathlib import Path

from .contracts import (AdvanceOptimizationInput, ComparisonInput, ConfigureOptimizationInput,
                        CreateVirtualInput, StrategyParameters)
from .evaluation import evaluate_completed, summarize_runs
from .reporting import comparison_csv, comparison_markdown, trajectory_csv, run_markdown
from .storage import dump


def run_comparison(lab, plan: ComparisonInput, folder, *, actor='human_authorized_comparison'):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)  # Preserve all previous experiment records.
    parameters = plan.model_dump()
    # Persist the plan BEFORE any response or comparison outcome is observed.
    (folder/'plan.json').write_text(json.dumps(parameters, ensure_ascii=False, indent=2))
    runs, environment_sha, initial_identity = [], None, None
    for seed in plan.seeds:
        for strategy in ('Random', 'Uniform', 'EI', 'UCB'):
            creation = {k: v for k, v in parameters.items() if k not in ('seeds', 'additional_budget', 'xi', 'kappa')}
            token = lab.authorize('CREATE_VIRTUAL', 'new', actor, confirmed=True)
            experiment = lab.create_virtual_experiment(CreateVirtualInput(**creation, seed=seed, authorization_id=token))
            eid = experiment.experiment_id
            token = lab.authorize('RUN_VIRTUAL_LOOP', eid, actor, confirmed=True)
            lab.configure_optimization(ConfigureOptimizationInput(experiment_id=eid,
                parameters=StrategyParameters(strategy=strategy, xi=plan.xi, kappa=plan.kappa),
                additional_budget=plan.additional_budget, authorization_id=token))
            lab.advance_optimization(AdvanceOptimizationInput(experiment_id=eid, rounds=plan.additional_budget,
                request_id='complete-budget', authorization_id=token))
            report = lab.export_report(eid)
            initial = [(f['candidate_id'].rsplit(':', 1)[-1], f['value']) for f in report['experiment']['initial_feedback']]
            if environment_sha is not None:
                assert report['environment_sha256'] == environment_sha and initial == initial_identity
            environment_sha, initial_identity = report['environment_sha256'], initial
            evaluation = evaluate_completed(lab, eid)
            name = f'{strategy}-seed-{seed}'
            (folder/(name+'.json')).write_text(json.dumps({'report': report, 'evaluation': evaluation}, ensure_ascii=False, indent=2))
            (folder/(name+'.csv')).write_text(trajectory_csv(report))
            (folder/(name+'.md')).write_text(run_markdown(report))
            runs.append({'strategy': strategy, 'seed': seed, 'experiment_id': eid,
                         'log': name+'.json', 'evaluation': evaluation})
    result = {'schema_version': 'm4e2-1', 'plan': parameters, 'environment_sha256': environment_sha,
              'initial_identity': initial_identity, 'runs': runs, 'summary': summarize_runs(runs),
              'fairness': {'same_pool_truth_initial_budget_cost_noise_seed_plan': True,
                           'noise_policy': 'fixed by environment noise_seed and candidate; no query-order randomness'},
              'LLM_agent_enabled': False, 'M5_enabled': False}
    for extension, content in [('json', json.dumps(result, ensure_ascii=False, indent=2)),
                               ('csv', comparison_csv(result)), ('md', comparison_markdown(result))]:
        (folder/('comparison.'+extension)).write_text(content)
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir()) if p.is_file()}
    (folder/'manifest.json').write_text(dump(manifest))
    return result
