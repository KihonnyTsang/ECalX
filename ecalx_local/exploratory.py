"""Offline adapter for existing artifacts. No fit, provider, or task mutation."""
import hashlib
import json
import math
from pathlib import Path

from . import SCIENCE_ROOT, PERSONAL
from .field_protocol import has_explicit_path, user_reference

MODELS = ('RandomForest', 'XGBoost')
LIMITATIONS = (
    '估计的是文献原报告 ΔT 标签，不是统一带符号的绝热温变，也不证明制冷效果。',
    'ΔE 只表示场变化幅度。例如 0→10 与 50→60 的 ΔE 都是 10，但偏置和历史不同；本模型不能预测这些明确路径。',
    '训练数据的样品、工艺、符号、仪器、速率与热损未统一；同配方不等于同一样品。',
    '验证是按组成与论文隔离的五折交叉验证，没有独立最终外测；平均误差不是本次预测的误差条。',
    '通过范围与近邻检查只允许内部探索，不证明新材料可靠插值。没有校准区间，不输出置信度或性能排名。',
    '直接测量法和间接推算法分别建模；不同方法或实验协议的数值不能直接比较或排名。',
    '仅支持训练覆盖的名义 ABO₃ 块体陶瓷；不支持薄膜、聚合物、器件或明确加载/撤场路径。ΔS 无有效预测器。',
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verified(root, path, expected):
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or digest(target) != expected:
        raise ValueError('artifact_sha_mismatch')
    return target


def model_bundle(root, method, model):
    """Match each artifact to its OWN data, metrics and split; never train."""
    import pandas as pd
    from ecalx.ml.dataset import FEATURES, TARGET
    root = Path(root)
    if method not in ('direct', 'indirect') or model not in MODELS:
        raise ValueError('unsupported_research_model')
    if model == 'RandomForest':
        card_path = root / 'docs/assumption_sensitivity_results.json'
        card = json.loads(card_path.read_text())
        from ecalx.ml.sensitivity import VERSION
        if card['version'] != VERSION:
            raise ValueError('unexpected_model_version')
        detail = card['experiments'][method]['scenarios']['original_restricted']
        artifact = detail['artifacts'][model]
        data_path, data_sha = detail['training_data_path'], detail['training_data_sha256']
        metrics = detail['metrics'][model]['common_A_test']
        features = detail['features']
        verified(root, detail['split_path'], detail['split_sha256'])
        version = VERSION + '/original_restricted'
        evaluation = '原 research_predict 配套、组成/DOI 隔离的五折 OOF（common A）'
    else:
        card_path = root / 'docs/model_card.json'
        card = json.loads(card_path.read_text())
        results = json.loads((root / 'docs/baseline_results.json').read_text())
        detail = results['experiments'][method]
        artifact = card['artifacts'][method][model]
        data_path, data_sha = f'data/processed/baseline_A_{method}.csv', detail['export_sha256']
        metrics = detail['metrics'][model]['all']
        features = card['features']
        verified(root, 'data/source_manifest.json', card['source_manifest_sha256'])
        version = 'M4-A/seed-' + str(card['seed'])
        evaluation = card['group_rule']
    if card['status'] != 'TRAINED_UNVERIFIED' or card['production_enabled'] or tuple(features) != FEATURES:
        raise ValueError('unexpected_model_contract')
    model_path = verified(root, artifact['path'], artifact['sha256'])
    train_path = verified(root, data_path, data_sha)
    # Validation provenance is checked too; metadata errors fail closed.
    oof_path = detail.get('oof_path', f'reports/baseline/{method}_oof.csv')
    verified(root, oof_path, detail['oof_sha256'])
    train = pd.read_csv(train_path)
    if set(train.method) != {method} or set(train.source) != {'A'}:
        raise ValueError('training_source_or_method_mismatch')
    metadata = {'model': model, 'method': method, 'model_version': version,
        'model_sha256': artifact['sha256'], 'model_path': artifact['path'],
        'training_data_path': data_path, 'training_data_sha256': data_sha,
        'model_card_sha256': digest(card_path), 'features': list(FEATURES),
        'target': TARGET, 'status': card['status'], 'evaluation': evaluation,
        'validation_metrics': metrics, 'oof_sha256': detail['oof_sha256'],
        'training_counts': {'rows': len(train), 'compositions': train.composition_key.nunique(),
                            'papers': train.doi.nunique()}}
    return train, model_path, metadata


def estimate(*, formula='', temperature_K=None, delta_E_kV_cm=None, method=None,
             model=None, sample_form=None, field_path='unknown', e_start=None,
             e_end=None, request=None, root=SCIENCE_ROOT, protocol=None, parent_protocol=None):
    """Reject before predict. Explicit conditions only; never infer a midpoint."""
    result = {'schema_version': 'local-exploratory-dt-1', 'status': 'REJECT',
        'prediction_origin': 'experimental_model', 'prediction_K': None,
        'prediction_interval': None, 'formal_delta_T': 'unknown', 'delta_S': 'unknown',
        'M5_enabled': False, 'performance_rank': None, 'experimental': True,
        'experimentally_verified': False, 'production_recommendation_enabled': False,
        'inputs': {'formula': formula, 'temperature_K': temperature_K,
            'delta_E_kV_cm': delta_E_kV_cm, 'method': method, 'model': model,
            'sample_form': sample_form, 'field_path': field_path, 'e_start': e_start, 'e_end': e_end},
        'field_protocol': user_reference(protocol), 'parent_field_protocol': user_reference(parent_protocol) if parent_protocol else None,
        'confirmed_goals': request.to_dict() if request is not None else None,
        'reasons': [], 'limitations': list(LIMITATIONS), 'domain': None, 'metadata': None}
    reasons = []
    # Path refusal takes priority, including partial endpoints and frozen goals.
    if field_path != 'unknown' or e_start is not None or e_end is not None:
        reasons.append('path_specific_prediction_not_supported')
    request_protocol = (getattr(request, 'raw_inputs', {}) or {}).get('field_protocol') if request else None
    if any(has_explicit_path(p) for p in (protocol, parent_protocol, request_protocol) if isinstance(p, dict)):
        reasons.append('path_specific_prediction_not_supported')
    # Refuse before artifact loading. An assumed path has the same unsupported
    # geometry as an observed path, even if a caller sends field_path=unknown.
    if reasons:
        return {**result, 'reasons': sorted(set(reasons))}
    if not PERSONAL:
        return {**result, 'status':'UNAVAILABLE', 'prediction_origin':None, 'reasons':['no_user_model_provided']}
    if request is not None:
        from ecalx.services.workflow import research_goal_rejections
        reasons += research_goal_rejections(request, formula, temperature_K, sample_form)
        if request.delta_E_kV_cm is not None and delta_E_kV_cm != request.delta_E_kV_cm:
            reasons.append('prediction_delta_E_does_not_match_confirmed_goals')
    missing = [k for k, v in [('formula', formula), ('temperature_K', temperature_K),
        ('delta_E_kV_cm', delta_E_kV_cm), ('method', method), ('model', model),
        ('sample_form', sample_form)] if v is None or v == '']
    if missing:
        reasons += ['missing:' + k for k in missing]
    try:
        from ecalx.ml.applicability_v2 import domain_contract, assess_candidate
        if model not in MODELS or method not in ('direct', 'indirect'):
            return {**result, 'status': 'NEEDS_INPUT' if missing else 'REJECT',
                    'reasons': reasons or ['unsupported_research_model']}
        train, path, metadata = model_bundle(root, method, model)
        contract = domain_contract(train, method)
        result.update(metadata=metadata, domain={'contract': contract, 'assessment': {
            'decision': 'NOT_EVALUATED', 'reasons': sorted(set(reasons))}})
        if reasons:
            return {**result, 'status': 'REJECT' if any(not r.startswith('missing:') for r in reasons) else 'NEEDS_INPUT',
                    'reasons': sorted(set(reasons))}
        assessment = assess_candidate(train, formula, temperature_K, delta_E_kV_cm,
            method=method, sample_form=sample_form, field_path=field_path, contract=contract)
        result.update(metadata=metadata, domain={'contract': contract, 'assessment': assessment})
        if assessment['decision'] == 'REJECT':
            return {**result, 'reasons': assessment['reasons']}
        if model == 'RandomForest':
            from ecalx.services.research import research_predict
            original = research_predict(root, acknowledged=True, formula=formula,
                temperature_K=temperature_K, delta_E_kV_cm=delta_E_kV_cm, method=method,
                scenario='original_restricted', sample_form=sample_form, request=request)
            result['original_research_receipt'] = original
            if original['status'] != 'RESEARCH_ONLY':
                return {**result, 'status': original['status'], 'reasons': original.get('reasons', [])}
            if original['model_sha256'] != metadata['model_sha256']:
                raise ValueError('research_predict_model_mismatch')
            value = original['prediction_K']
        else:
            import joblib
            import pandas as pd
            from ecalx.ml.dataset import ELEMENTS, FEATURES, TARGET, feature_matrix
            from ecalx.materials.chemistry import parse_formula
            payload = joblib.load(path)
            if (payload['status'] != 'TRAINED_UNVERIFIED' or payload['method'] != method
                    or payload['target'] != TARGET or tuple(payload['features']) != FEATURES):
                raise ValueError('serialized_model_contract_mismatch')
            amounts = parse_formula(formula)
            total = sum(amounts.values())
            row = {**{'fraction_'+e: float(amounts.get(e, 0)/total) for e in ELEMENTS},
                   'temperature_K': temperature_K, 'delta_E_kV_cm': delta_E_kV_cm}
            value = float(payload['estimator'].predict(feature_matrix(pd.DataFrame([row])))[0])
        if not math.isfinite(value):
            raise ValueError('nonfinite_prediction')
        return {**result, 'status': 'RESEARCH_ONLY', 'prediction_K': value}
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {**result, 'status': 'UNAVAILABLE', 'reasons': ['artifact_or_input_error:' + str(error)]}


def reason_text(reason):
    labels = {'formula': '材料组成', 'temperature_K': '明确的评价温度点（不会自动取温区中点）',
        'delta_E_kV_cm': 'ΔE 场变化幅度', 'method': '直接法或间接法', 'model': '模型',
        'sample_form': '样品形态'}
    if reason.startswith('missing:'):
        return '请补充' + labels.get(reason[8:], reason[8:]) + '。'
    messages = {
        'path_specific_prediction_not_supported': '当前模型不支持明确场路径；即使 0→ΔE 也不能输出数值。',
        'requested_field_protocol_or_bounds_not_supported_by_research_model': '本研究指定了场路径或电场上下限，不能将它改成 ΔE 送入当前模型。',
        'prediction_delta_E_does_not_match_confirmed_goals': '填写的 ΔE 与原研究条件不一致。',
        'prediction_temperature_does_not_match_confirmed_goals': '评价温度点不在本研究的温度范围内。',
        'prediction_elements_do_not_match_confirmed_goals': '组成不满足本研究的元素要求。',
        'requested_material_type_not_supported_by_research_model': '本研究的材料类型不受当前模型支持。',
        'outside_training_T': '评价温度超出该方法模型的训练范围。',
        'outside_training_delta_E': 'ΔE 超出该方法模型的训练范围。',
        'outside_training_composition_geometry': '组成离训练样本过远，超过 M4-C 的近邻距离门槛。',
        'unsupported_sample_form': '当前模型仅覆盖块体陶瓷，不支持此样品形态。',
        'unsupported_material_family_stoichiometry': '组成不属于当前模型覆盖的名义 ABO₃ 计量族。',
        'unknown_model_elements': '组成含模型未支持的元素。',
        'invalid_formula': '化学式无法解析。', 'invalid_T': '温度必须是大于 0 K 的有限数值。',
        'invalid_delta_E': 'ΔE 必须是大于 0 的有限数值。',
        'insufficient_training_geometry': '训练组成数量不足，不能检查近邻适用域。',
    }
    if reason.startswith('composition_outside_training_bounds:'):
        return reason.split(':')[1] + ' 的原子分数超出训练范围。'
    if reason.startswith('element_absent_from_training_fold:'):
        return '该方法的训练集没有 ' + reason.split(':')[1] + '，不能量化。'
    if reason.startswith('artifact_or_input_error:'):
        return '模型、数据或验证工件核验未通过，暂不提供数值。请查看高级详情。'
    return messages.get(reason, '条件不受当前模型支持，请查看高级详情。')


# Explicit, editable examples. Values are not cached predictions or user defaults.
DEMOS = {
    '模型接受': {'formula': 'BaTi0.89Sn0.11O3', 'temperature': '343', 'delta_E': '10', 'method': 'direct', 'sample_form': 'bulk ceramic', 'path': '仅 ΔE（路径未知）'},
    '拒答：缺评价点': {'formula': 'BaTi0.89Sn0.11O3', 'temperature': '', 'delta_E': '10', 'method': 'direct', 'sample_form': 'bulk ceramic', 'path': '仅 ΔE（路径未知）'},
    '超适用域': {'formula': 'BaTi0.89Sn0.11O3', 'temperature': '900', 'delta_E': '10', 'method': 'direct', 'sample_form': 'bulk ceramic', 'path': '仅 ΔE（路径未知）'},
    '明确场路径不支持': {'formula': 'BaTi0.89Sn0.11O3', 'temperature': '343', 'delta_E': '10', 'method': 'direct', 'sample_form': 'bulk ceramic', 'path': '明确加载路径', 'e_start': '0', 'e_end': '10'},
}
