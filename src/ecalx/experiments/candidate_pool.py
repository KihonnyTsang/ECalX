"""M3.5 -> virtual pool bridge. Exact membership only; no labels or legacy ML calls."""
import csv
import hashlib
import io
from fractions import Fraction
from pathlib import Path

from ecalx.materials.chemistry import parse_formula, exact_key
from ecalx.materials.generator import REGISTRY
from .adapters import BSMT_SHA256
from .contracts import ExperimentError


def public_domain(adapter, root):
    if adapter == 'SIMULATED':
        return [{'key': f'sim-{i:03}', 'identity': exact_key({e: v for e, v in
                 {'Ba': 1-Fraction(i, 20), 'Sr': Fraction(i, 20), 'Ti': Fraction(1), 'O': Fraction(3)}.items() if v})}
                for i in range(21)]
    if adapter != 'HISTORICAL_REPLAY':
        raise ExperimentError('候选桥仅支持两种虚拟模式')
    raw = (Path(root)/'docs/bsmt_results/measurements.csv').read_bytes()
    if hashlib.sha256(raw).hexdigest() != BSMT_SHA256:
        raise ExperimentError('历史组成登记SHA不符')
    # Only formula is projected; labels are never returned or used to select membership.
    return [{'key': f'history-{i:03}', 'identity': exact_key(parse_formula(row['formula']))}
            for i, row in enumerate(csv.DictReader(io.StringIO(raw.decode()))) ]


def generated_candidate_pool(generation, adapter, root):
    """Accepts both generator.generate and automatic.explore_materials result dictionaries.

    Returns keys for CreateVirtualInput(candidate_keys=...). Never mutates generation,
    marks it measured, or authorizes legacy research predictions.
    """
    domain = {r['identity']: r['key'] for r in public_domain(adapter, root)}
    accepted, rejected, keys = [], [], set()
    for candidate in generation.get('candidates', []):
        cid = candidate.get('candidate_id')
        try:
            exact = candidate.get('stoichiometry_exact')
            amounts = ({e: Fraction(n) for e, n in exact.items()} if exact
                       else parse_formula(candidate.get('formula_raw') or candidate['formula']))
            if any(n <= 0 for n in amounts.values()):
                raise ValueError('非正化学计量')
            family = candidate.get('family_rule')
            if family not in REGISTRY.rules:
                raise ValueError('缺少受支持的化学规则，不能跳过化学检查')
            REGISTRY.rules[family].validate(amounts)
            identity = exact_key(amounts)
            if identity not in domain:
                raise ValueError('不在模拟器冻结21点定义域中（不插值）' if adapter == 'SIMULATED'
                                 else '无对应真实历史标签；仅12个BSMT组成可查询')
            key = domain[identity]
            if key in keys:
                raise ValueError('重复名义组成；不能重复消费查询预算')
            keys.add(key)
            accepted.append({'generated_candidate_id': cid, 'candidate_key': key,
                             'original_status': candidate.get('status'), 'virtual_only': True})
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as error:
            rejected.append({'generated_candidate_id': cid, 'reason': str(error)})
    return {'adapter': adapter, 'candidate_keys': sorted(keys), 'accepted': accepted, 'rejected': rejected,
            'performance_verification_updated': False, 'legacy_model_applicability_changed': False,
            'notice': '仅虚拟环境定义域准入；不授予真实模型预测权限，不写入历史实测数据库'}
