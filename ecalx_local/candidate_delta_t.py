"""Read-only candidate links to existing calculators and opt-in model receipts.

Literature cooling magnitudes, research model labels and M4 scores stay separate.
This module never trains, loads private models in BYO, or changes a task.
"""
from decimal import Decimal
import math
import re

from ecalx.materials.chemistry import exact_key, parse_formula
from . import PERSONAL
from .bst_tool import BSTUnavailable, load_interpolator
from .field_protocol import CUSTOM, has_explicit_path, reference_protocol


def evaluation_conditions(task):
    """Use an explicit point or the latest run; never invent a range midpoint."""
    goals = task.get('frozen_goals', {})
    runs = task.get('simulation_runs') or []
    run = runs[-1] if runs else None
    settings = run['report']['experiment']['conditions'] if run else {}
    lo, hi = goals.get('temperature_min_K'), goals.get('temperature_max_K')
    temperature = lo if lo is not None and lo == hi else settings.get('temperature_K')
    start, end = goals.get('e_start_kV_cm'), goals.get('e_end_kV_cm')
    protocol = task.get('input_snapshot', {}).get('field_protocol') or reference_protocol()
    if start is None and end is None and has_explicit_path(protocol):
        start, end = protocol.get('start_kV_cm'), protocol.get('end_kV_cm')
    if start is None and end is None and not has_explicit_path(protocol) and run:
        start, end = settings.get('e_start_kV_cm'), settings.get('e_end_kV_cm')
    delta = goals.get('delta_E_kV_cm')
    if delta is None:
        delta = abs(end-start) if start is not None and end is not None else protocol.get('delta_kV_cm')
    errors = []
    if temperature is not None and (not math.isfinite(temperature) or temperature <= 0
            or (lo is not None and temperature < lo) or (hi is not None and temperature > hi)):
        errors.append('评价温度与研究条件冲突')
    if run:
        for key, value in [('e_start_kV_cm', start), ('e_end_kV_cm', end)]:
            if value is not None and settings.get(key) != value:
                errors.append('最新模拟与研究场路径不同')
        if temperature is not None and temperature != settings.get('temperature_K'):
            errors.append('最新模拟与研究温度不同')
    if delta is not None and (not math.isfinite(delta) or delta <= 0
            or (start is not None and end is not None and not math.isclose(delta, abs(end-start)))):
        errors.append('场变化幅度无效或与路径冲突')
    if not errors and (start is not None or end is not None):
        protocol = reference_protocol(CUSTOM, start=start, end=end, delta=delta)
    return {'temperature_K': temperature, 'delta_E_kV_cm': delta,
            'e_start_kV_cm': start, 'e_end_kV_cm': end, 'field_protocol': protocol,
            'source': 'latest_simulation_run' if run else 'frozen_research_goals',
            'simulation_run_id': run['experiment_id'] if run else None, 'errors': errors}


def _bst_fraction(formula):
    amounts = parse_formula(formula)
    ti = amounts.get('Ti')
    if (set(amounts) != {'Ba', 'Sr', 'Ti', 'O'} or not ti
            or amounts['O'] != 3*ti or amounts['Ba'] + amounts['Sr'] != ti):
        raise ValueError('仅支持未掺杂的 Ba(1−x)Sr(x)TiO3；含 Ca 等其他元素不能外推。')
    sr = amounts['Sr']/ti
    return Decimal(sr.numerator)/Decimal(sr.denominator)


def _model_state(task, conditions, receipts):
    if not PERSONAL:
        reason = '当前 BYO 未配置模型文件。'
        if has_explicit_path(conditions['field_protocol']):
            reason += '原模型也不支持本次明确场路径。'
        return {'status': 'unavailable', 'reason': reason, 'receipts': []}
    if conditions['errors'] or has_explicit_path(conditions['field_protocol']) or any(
            task.get('frozen_goals', {}).get(k) is not None for k in ('e_min_kV_cm', 'e_max_kV_cm')):
        return {'status': 'rejected', 'reason': 'RF/XGBoost 原适用域不支持明确电场路径或电场上下限。', 'receipts': []}
    # Read only results explicitly linked to this task by the original estimator.
    if receipts is None:
        from .conversation import estimate_receipts
        receipts = estimate_receipts(task)
    return {'status': 'no_receipt', 'reason': '尚无通过原适用域且关联本研究的估计；可在详情中选择条件和模型运行。',
            'receipts': receipts}


def candidate_results(task, cards, *, root=None, receipts=None):
    conditions = evaluation_conditions(task)
    model_state = _model_state(task, conditions, receipts)
    # One small checksum-verified reference read for the whole page, no ML work.
    model, load_error = None, None
    results = {}
    for card in cards:
        result = {'conditions': conditions, 'literature': {'status': 'unavailable', 'point': None},
                  'model': {**model_state, 'receipts': []}, 'primary': None}
        literature = result['literature']
        try:
            x = _bst_fraction(card['formula'])
            material = ((task.get('frozen_goals', {}).get('material_type') or '')+' '+
                        task.get('input_snapshot', {}).get('raw', {}).get('natural_language', '')).replace('_', ' ').replace('-', ' ')
            if re.search(r'薄膜|聚合物|单晶|thin\s*film|polymer|single\s*crystal', material, re.I):
                raise ValueError('BST 文献参考仅覆盖块体陶瓷，不适用于本研究的样品形态。')
            if conditions['errors']:
                raise ValueError('；'.join(conditions['errors']))
            if conditions['temperature_K'] is None or conditions['delta_E_kV_cm'] is None:
                raise ValueError('需要明确的单点评价温度与场变化幅度；不自动取温区中点。')
            if model is None and load_error is None:
                try:
                    model = load_interpolator(root=root)
                except BSTUnavailable as error:
                    load_error = str(error)
            if load_error:
                raise ValueError(load_error)
            point = model.predict(x, conditions['temperature_K'], conditions['delta_E_kV_cm'])
            literature.update(status='reference_only', point=point,
                sample_form_scope='bulk_ceramic', actual_candidate_form_verified=False,
                protocol_match=False, experimentally_measured=False,
                reason='仅作 BST 块体陶瓷文献参考；标称撤场正温降幅值，完整波形未核实，不能代表本次协议下的温变。')
        except ValueError as error:
            literature['reason'] = str(error)
        if model_state['status'] == 'no_receipt':
            from .conversation import fingerprint, estimate_goals
            matching = []
            for receipt in model_state['receipts']:
                inp = receipt.get('inputs', {})
                try:
                    same_formula = exact_key(parse_formula(inp.get('formula', ''))) == exact_key(parse_formula(card['formula']))
                except ValueError:
                    same_formula = False
                if (same_formula and receipt.get('status') == 'RESEARCH_ONLY'
                        and receipt.get('execution') == 'OFFLINE_EXISTING_MODEL_INFERENCE'
                        and not receipt.get('demo') and receipt.get('parent_task_id') == task['id']
                        and receipt.get('parent_goals_sha256') == fingerprint(task['frozen_goals'])
                        and estimate_goals(receipt) == task['frozen_goals']
                        and inp.get('temperature_K') == conditions['temperature_K']
                        and inp.get('delta_E_kV_cm') == conditions['delta_E_kV_cm']
                        and inp.get('field_path') == 'unknown' and inp.get('e_start') is None and inp.get('e_end') is None
                        and not has_explicit_path(receipt.get('field_protocol'))
                        and not has_explicit_path(receipt.get('parent_field_protocol'))
                        and ((receipt.get('domain') or {}).get('assessment') or {}).get('decision') == 'INTERNAL_EXPLORATION_ONLY'
                        and (receipt.get('metadata') or {}).get('model') in ('RandomForest', 'XGBoost')
                        and (receipt.get('metadata') or {}).get('status') == 'TRAINED_UNVERIFIED'
                        and (receipt.get('metadata') or {}).get('model_sha256')
                        and inp.get('method') in ('direct', 'indirect') and inp.get('sample_form') == 'bulk ceramic'
                        and receipt.get('prediction_K') is not None and math.isfinite(receipt['prediction_K'])):
                    matching.append(receipt)
            if matching:
                result['model'].update(status='research_only', receipts=matching, reason='研究性模型估计；文献原报告 ΔT 标签，未独立验证。')
                result['primary'] = max(matching, key=lambda r: r.get('executed_at', ''))
        results[card['id']] = result
    return results


def report_lines(task, cards):
    lines = ['', '## 候选 ΔT 计算连接', '',
             'M4 排名依据仍为模拟评分。文献撤场正温降参考与 RF/XGBoost 文献标签估计分别展示，不混合排序。']
    results = candidate_results(task, cards)
    for card in cards:
        result = results[card['id']]
        primary, literature = result['primary'], result['literature']
        lines += ['', '### '+card['formula'],
                  f"本次协议 ΔT：{primary['prediction_K']:.3f} K（研究性模型估计）" if primary else '本次协议 ΔT：暂无可用ΔT估计',
                  'RF/XGBoost：'+result['model']['reason']]
        if literature['point']:
            p = literature['point']
            lines += [f"独立文献插值参考 ΔT：{p['delta_T_magnitude_K']:.3f} K（正温降幅值）；{p['temperature_K']:g} K，标称撤场幅值 {p['nominal_delta_E_kV_cm']:g} kV/cm。",
                      literature['reason'], '来源 DOI：'+p['source_doi']+'；CSV SHA256：'+p['source_csv_sha256']]
        else:
            lines += ['文献插值不可用：'+literature['reason']]
    return lines
