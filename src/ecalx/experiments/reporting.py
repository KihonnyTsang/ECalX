"""Portable visible-data CSV and Markdown exports; no hidden oracle access."""
import csv
import io
import json
from ecalx.services.export import safe_csv_cell


def trajectory_csv(report):
    fields = ['round', 'candidate_id', 'formula', 'strategy', 'strategy_parameters', 'reason',
              'prediction_before', 'posterior_std_before', 'feedback_K', 'provenance', 'source',
              'cumulative_best', 'prequential_absolute_error_K', 'model_before', 'model_after',
              'training_sha256_after', 'frozen_sha256', 'environment_sha256', 'query_budget']
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for step in report['loop']['trace']:
        row = {k: step.get(k) for k in fields}
        row.update(feedback_K=step['feedback']['value'], source=step['feedback']['source'])
        writer.writerow({k: safe_csv_cell(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                         for k, v in row.items()})
    return out.getvalue()


def run_markdown(report):
    e = report['experiment']
    lines = ['# ECalX M4-E2 虚拟实验报告', '', f"来源：{e['adapter']}；seed={e['seed']}。",
             'SIMULATED为数学演示；HISTORICAL_REPLAY为12组成/单论文已曝光回放。非盲测或真实材料发现；M5禁用。',
             '', '```json', json.dumps(report.get('optimization'), ensure_ascii=False, indent=2), '```', '',
             '| 轮次 | 候选 | 策略 | 反馈K | 累计最佳K | 模型版本 |', '|---|---|---|---:|---:|---|']
    for r in report['loop']['trace']:
        lines.append(f"| {r['round']} | {r['formula']} | {r['strategy']} | {r['feedback']['value']:.8g} | {r['cumulative_best']:.8g} | {r['model_after']} |")
    lines += ['', '完整参数、选择依据、来源、训练SHA和预算见配套JSON/CSV。GP方差未经校准，不能作为可信物性区间。']
    return '\n'.join(lines)+'\n'


def comparison_csv(comparison):
    out = io.StringIO()
    fields = ['strategy', 'seed', 'round', 'total_cost', 'cumulative_best_observed_K', 'simple_regret_K']
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for r in comparison['runs']:
        for point in r['evaluation']['series']:
            writer.writerow({k: {'strategy': r['strategy'], 'seed': r['seed']}.get(k, point.get(k)) for k in fields})
    return out.getvalue()


def comparison_markdown(comparison):
    lines = ['# ECalX 策略比较', '', f"来源：{comparison['plan']['adapter']}；冻结环境SHA={comparison['environment_sha256']}。",
        '相同候选池、初始样本、查询成本、预算、固定噪声及seed计划。所有运行均报告，不挑选最佳一次。', '',
        '| 策略 | 次数 | 平均最佳观测K | 样本标准差K | 相对Random配对均值差K |', '|---|---:|---:|---:|---:|']
    for s in comparison['summary']:
        std = '不可评价' if s['sample_std_K'] is None else f"{s['sample_std_K']:.8g}"
        lines.append(f"| {s['strategy']} | {s['runs']} | {s['mean_best_observed_K']:.8g} | {std} | {s['paired_mean_difference_from_random_K']:.8g} |")
    lines += ['', '单一演示环境中seed分布仅为描述统计，不是独立优势验证；历史仅12组成/1论文/已曝光标签，不声称跨论文泛化。',
              'Simple regret仅对冻结模拟器有限池潜在真值定义；历史regret/独立准确率/统计优势不可评价。', '',
              '```json', json.dumps(comparison['plan'], ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines)+'\n'
