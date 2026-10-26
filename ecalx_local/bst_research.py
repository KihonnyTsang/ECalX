"""Condition-based adapter to the unchanged BST calculator; no general gate."""
import hashlib
import json
import re
from decimal import Decimal

from ecalx.materials.chemistry import parse_formula
from .bst_tool import BSTUnavailable, load_interpolator
from .bst_reference import DATASET_ID


def composition(prepared):
    raw = prepared.get('raw', {})
    explicit = raw.get('composition', '').strip()
    if explicit:
        return explicit
    tokens = re.findall(r'[A-Z][A-Za-z0-9.()₀-₉]*', raw.get('natural_language', ''))
    formulas = []
    for token in tokens:
        try:
            if len(parse_formula(token)) > 1:
                formulas.append(token)
        except ValueError:
            pass
    return formulas[0] if len(set(formulas)) == 1 else ''


def eligibility(prepared, *, root=None):
    formula = composition(prepared)
    try:
        amounts = parse_formula(formula)
        ti = amounts['Ti']
        if (set(amounts) != {'Ba', 'Sr', 'Ti', 'O'} or amounts['O'] != 3*ti
                or amounts['Ba'] + amounts['Sr'] != ti):
            raise ValueError()
        sr = amounts['Sr'] / ti
        x = Decimal(sr.numerator) / Decimal(sr.denominator)
    except (ValueError, KeyError):
        return {'status': 'not_bst', 'notice': '文献曲线插值仅适用于BST块体陶瓷；当前材料继续使用正常研究流程。'}
    goals = prepared['goals']
    t = goals['temperature_min_K']
    e = goals['delta_E_kV_cm']
    if t is None or t != goals['temperature_max_K'] or e is None:
        return {'status': 'incomplete', 'notice': 'BST文献插值需单一温度和ΔE；可将最低与最高温度填为同一个值。研究条件仍可自由填写。'}
    protocol = prepared.get('field_protocol', {})
    material = (goals.get('material_type') or '') + prepared.get('raw', {}).get('natural_language', '')
    endpoints = (goals['e_start_kV_cm'], goals['e_end_kV_cm'])
    if (re.search(r'薄膜|聚合物|单晶|thin\s*film|polymer|single\s*crystal', material, re.I)
            or protocol.get('kind') == 'standard_pulse'
            or (any(v is not None for v in endpoints) and endpoints != (e, 0))
            or set(goals.get('exclude_elements', [])) & {'Ba', 'Sr', 'Ti', 'O'}
            or set(goals.get('include_elements', [])) - {'Ba', 'Sr', 'Ti', 'O'}
            or (goals.get('e_max_kV_cm') is not None and goals['e_max_kV_cm'] < e)
            or (goals.get('e_min_kV_cm') is not None and goals['e_min_kV_cm'] > 0)):
        return {'status': 'unsupported', 'notice': '研究条件与BST块体陶瓷标称撤场参考不适用；不输出ΔT，正常研究仍可继续。'}
    try:
        model = load_interpolator(root=root)
    except BSTUnavailable as error:
        return {'status': 'missing_data', 'notice': str(error)}
    try:
        _, _, _, lo, hi = model.coverage(x, e)
        if not lo <= Decimal(str(t)) <= hi:
            raise ValueError(f'当前共同温度覆盖为{lo}–{hi} K。')
    except ValueError as error:
        return {'status': 'unsupported', 'notice': '条件超出文献曲线插值范围：'+str(error)+' 正常研究仍可继续。'}
    return {'status': 'available', 'formula': formula, 'x': str(x), 'temperature_K': t,
            'field_kV_cm': e, 'temperature_coverage_K': [float(lo), float(hi)]}


def signature(prepared):
    return hashlib.sha256(json.dumps({'raw': prepared['raw'], 'goals': prepared['goals'],
        'protocol': prepared.get('field_protocol')}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def calculate(prepared, *, root=None):
    status = eligibility(prepared, root=root)
    if status['status'] != 'available':
        raise ValueError(status['notice'])
    model = load_interpolator(root=root)
    x, t, e = status['x'], status['temperature_K'], status['field_kV_cm']
    return {'label': '文献曲线插值', 'dataset_id': DATASET_ID,
            'input_sha256': signature(prepared), 'experimentally_measured': False,
            'general_ai_prediction': False, 'sample_form_scope': 'bulk_ceramic',
            'point': model.predict(x, t, e),
            'curve': model.curve(x, e, include_temperature=t),
            'references': model.reference_rows(x, e)}
