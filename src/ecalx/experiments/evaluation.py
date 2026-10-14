"""Post-run evaluator. Oracle access is denied until the frozen budget completes.

Never imported by strategies/optimization. Results are separate from execution state.
"""
import math

import numpy as np
from .contracts import ExperimentError


def evaluate_completed(lab, eid):
    plan = lab.optimization_state(eid)
    if not plan or plan['status'] != 'COMPLETED' or plan['consumed'] != plan['additional_budget']:
        raise ExperimentError('评价器只能在冻结实验预算全部完成后访问模拟真值')
    report = lab.export_report(eid)
    experiment, trace = report['experiment'], report['loop']['trace']
    initial = experiment['initial_feedback']
    best = max(r['value'] for r in initial)
    series = [{'round': 0, 'total_cost': len(initial), 'cumulative_best_observed_K': best}]
    series += [{'round': r['round'], 'total_cost': r['observations'],
                'cumulative_best_observed_K': r['cumulative_best']} for r in trace]
    result = {'status': 'POST_COMPLETION_ONLY', 'provenance': experiment['adapter'], 'series': series,
              'best_observed_K': series[-1]['cumulative_best_observed_K'],
              'total_query_cost': len(report['observations']), 'query_unit_cost': 1,
              'mean_best_observed_over_budget_K': float(np.mean([r['cumulative_best_observed_K'] for r in series])),
              'prequential_MAE_K': float(np.mean([r['prequential_absolute_error_K'] for r in trace])),
              'independent_generalization': {'status': 'NOT_EVALUABLE', 'value': None,
                  'reason': 'toy simulator or single-paper exposed history; no independent material validation'},
              'simple_regret_K': None, 'prediction_error_by_round': None,
              'queries_to_95_percent_available_latent_gain': None}
    if experiment['adapter'] != 'SIMULATED':
        result['scope'] = '12 labeled BSMT compositions only; one paper, exposed labels; descriptive replay'
        result['simple_regret_status'] = 'NOT_EVALUABLE: no defined physical global optimum for historical materials'
        result['prediction_error_status'] = 'Only selected-point prequential errors; cross-paper accuracy NOT_EVALUABLE'
        return result
    # The one oracle read happens after completion. Optimizer receives none of this.
    with lab.connect() as db:
        _, hidden, _ = lab._environment(db, eid)
    truth = {cid: label.get('latent_value', label['value']) for cid, label in hidden.items()}
    optimum = max(truth.values())
    queried = {r['candidate_id'] for r in initial}
    initial_latent_best = max(truth[cid] for cid in queried)
    errors = []
    for index, point in enumerate(series):
        if index:
            step = trace[index-1]
            queried.add(step['candidate_id'])
            unseen = set(truth) - set(step['training_ids_before'])
            residual = [step['predictions_before'][cid]-truth[cid] for cid in sorted(unseen)]
            errors.append({'round': step['round'], 'model_version': step['model_before'],
                           'unqueried_count_before': len(unseen), 'MAE_K': float(np.mean(np.abs(residual))),
                           'RMSE_K': float(math.sqrt(np.mean(np.square(residual)))),
                           'scope': 'post-run simulator latent oracle, unqueried BEFORE selection; not external accuracy'})
        point['best_queried_latent_K'] = max(truth[cid] for cid in queried)
        point['simple_regret_K'] = max(0., optimum-point['best_queried_latent_K'])
    threshold = initial_latent_best + .95*(optimum-initial_latent_best)
    reached = next((r['total_cost'] for r in series if r['best_queried_latent_K'] >= threshold), None)
    result.update({'scope': 'maximum over this frozen finite simulator pool; not a continuum or physical optimum',
                   'latent_pool_optimum_K': optimum, 'simple_regret_K': series[-1]['simple_regret_K'],
                   'simple_regret_status': 'DEFINED_FINITE_SIMULATOR_POOL', 'prediction_error_by_round': errors,
                   'queries_to_95_percent_available_latent_gain': reached,
                   'efficiency_threshold_latent_K': threshold,
                   'efficiency_status': 'REACHED' if reached is not None else 'NOT_REACHED_WITHIN_BUDGET'})
    return result


def summarize_runs(runs):
    summaries = []
    for strategy in ('Random', 'Uniform', 'EI', 'UCB'):
        selected = [r for r in runs if r['strategy'] == strategy]
        values = [r['evaluation']['best_observed_K'] for r in selected]
        if not values:
            continue
        n = len(values)
        summaries.append({'strategy': strategy, 'runs': n, 'mean_best_observed_K': float(np.mean(values)),
            'sample_std_K': float(np.std(values, ddof=1)) if n >= 3 else None,
            'distribution_status': 'DESCRIPTIVE_SEED_DISTRIBUTION' if n >= 3 else 'NOT_EVALUABLE: fewer than 3 seeds',
            'min_K': min(values), 'median_K': float(np.median(values)), 'max_K': max(values),
            'p10_K': float(np.quantile(values, .1)) if n >= 3 else None,
            'p90_K': float(np.quantile(values, .9)) if n >= 3 else None,
            'per_seed_best_observed_K': [{'seed': r['seed'], 'value': r['evaluation']['best_observed_K']} for r in selected],
            'mean_simple_regret_K': (float(np.mean([r['evaluation']['simple_regret_K'] for r in selected]))
                                    if all(r['evaluation']['simple_regret_K'] is not None for r in selected) else None),
            'mean_prequential_MAE_K': float(np.mean([r['evaluation']['prequential_MAE_K'] for r in selected])),
            'mean_budget_curve_K': float(np.mean([r['evaluation']['mean_best_observed_over_budget_K'] for r in selected]))})
    random_by_seed = {r['seed']: r for r in runs if r['strategy'] == 'Random'}
    for summary in summaries:
        paired = [r['evaluation']['best_observed_K']-random_by_seed[r['seed']]['evaluation']['best_observed_K']
                  for r in runs if r['strategy'] == summary['strategy']]
        summary['paired_mean_difference_from_random_K'] = float(np.mean(paired))
        summary['inferential_superiority'] = {'status': 'NOT_EVALUABLE', 'value': None,
            'reason': 'seeds vary selection/ties in one fixed environment, not independent scientific datasets'}
    return summaries
