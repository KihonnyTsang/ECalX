"""Read-only reported-label retrieval shared by BYO and personal adapters.

Source amplitude is usable without endpoints. Recommendation eligibility,
verified protocols and source-reported labels remain separate questions.
"""
from collections import defaultdict
from dataclasses import replace
import math

from ecalx.services import discovery as original
from ecalx.materials.formulas import formula_elements

ENDPOINTS = {'e_start_kV_cm', 'e_end_kV_cm'}


def amplitude(record):
    explicit = getattr(record, 'e_delta_kV_cm', None)
    if explicit is not None:
        return explicit
    if record.e_reported_kind == 'delta' and record.e_reported_kV_cm is not None:
        return record.e_reported_kV_cm
    if record.e_start_kV_cm is not None and record.e_end_kV_cm is not None:
        return abs(record.e_end_kV_cm-record.e_start_kV_cm)
    return None


def requested_amplitude(request):
    if request.delta_E_kV_cm is not None:
        return request.delta_E_kV_cm
    if request.e_start_kV_cm is not None and request.e_end_kV_cm is not None:
        return abs(request.e_end_kV_cm-request.e_start_kV_cm)
    return None


def amplitude_matches(record, request):
    actual, wanted = amplitude(record), requested_amplitude(request)
    return actual is not None and wanted is not None and math.isclose(actual, wanted, abs_tol=1e-9)


def path_unknown(record):
    return (record.e_start_kV_cm is None or record.e_end_kV_cm is None
            or record.field_path == 'unknown')


def unmet_constraints(record, request):
    failures = original.unmet_constraints(record, request)
    if request.delta_E_kV_cm is not None:
        failures = [f for f in failures if f != '电场变化幅度']
        if not amplitude_matches(record, request):
            failures.append('电场变化幅度')
    return sorted(set(failures))


def auxiliary_match(record, request):
    """Relax missing endpoints only, not contradictory endpoints or other goals."""
    failures = set(unmet_constraints(record, request))
    if not failures or failures-ENDPOINTS or not amplitude_matches(record, request):
        return False
    return all(getattr(record, key) is None for key in failures)


def usable_reported_label(record):
    return (amplitude(record) is not None and record.temperature_K is not None
            and record.delta_T_ad_K is not None and math.isfinite(record.delta_T_ad_K)
            and formula_elements(record.formula_normalized or record.formula_raw) is not None
            and 'quarantined' not in record.quality_flags)


def match_counts(records, request, verified=None):
    wanted = requested_amplitude(request)
    hits = [r for r in records if amplitude_matches(r, request)] if wanted is not None else []
    auxiliary = [r for r in records if auxiliary_match(r, request)]
    context = [r for r in hits if not unmet_constraints(r, replace(request,
        e_start_kV_cm=None, e_end_kV_cm=None, e_min_kV_cm=None, e_max_kV_cm=None))]
    verified_rows = verified.get('records', []) if verified and verified.get('status') == 'verified_readonly' else []
    verified_ids = {r['record_id'] for r in verified_rows}
    return {'requested_delta_E_kV_cm': wanted,
        'delta_E_matches': len(hits) if wanted is not None else None,
        'delta_E_context_matches': len(context) if wanted is not None else None,
        'delta_E_matches_path_unknown': sum(path_unknown(r) and r.record_id not in verified_ids for r in hits) if wanted is not None else None,
        'reported_label_matches': sum(usable_reported_label(r) for r in records if not unmet_constraints(r, request)),
        'amplitude_only_auxiliary_matches': len(auxiliary),
        'path_unknown_records': sum(path_unknown(r) and r.record_id not in verified_ids for r in records),
        'path_fields_complete_records': sum(not path_unknown(r) for r in records),
        'verified_path_records': len(verified_ids),
        'verified_path_matches': sum(not r['unmet_conditions'] for r in verified_rows),
        'verified_path_scope': '独立核验覆盖层（不改写原记录）；端点和分支核实不等于所有实验条件均已核实',
        'amplitude_count_scope': 'ΔE幅度匹配按全库计数；条件内幅度匹配另应用温度、组成、形态等要求。辅助证据不计入严格路径匹配。'}


def descriptive_statistics(items):
    """Arithmetic of reported labels, partitioned by source and physical basis."""
    groups = defaultdict(list)
    for item in items:
        r = item['evidence']
        if r.get('delta_T_ad_K') is None or r.get('temperature_K') is None:
            continue
        de = r.get('e_delta_kV_cm')
        if de is None and r.get('e_reported_kind') == 'delta':
            de = r.get('e_reported_kV_cm')
        if de is None and r.get('e_start_kV_cm') is not None and r.get('e_end_kV_cm') is not None:
            de = abs(r['e_end_kV_cm']-r['e_start_kV_cm'])
        if de is None or 'quarantined' in r.get('quality_flags', []):
            continue
        key = (r.get('formula_normalized') or r['formula_raw'], r['temperature_K'], de,
            r['measurement_method'], r.get('delta_t_basis'), r.get('delta_t_sign_convention'),
            r.get('sample_form'), r.get('doi') or r.get('url') or r.get('source_id') or r['record_id'],
            r.get('e_start_kV_cm'), r.get('e_end_kV_cm'), r.get('field_path'),
            (r.get('raw_metadata_json') or {}).get('protocol_fingerprint'), item['match_scope'])
        groups[key].append(r)
    result = []
    for key, rows in groups.items():
        values = [r['delta_T_ad_K'] for r in rows]
        result.append({'formula':key[0], 'temperature_K':key[1], 'delta_E_kV_cm':key[2],
            'method':key[3], 'delta_t_basis':key[4], 'sign_convention':key[5], 'sample_form':key[6],
            'source':key[7], 'match_scope':key[-1], 'record_count':len(rows),
            'reported_delta_T_min_K':min(values), 'reported_delta_T_max_K':max(values),
            'reported_delta_T_mean_K':sum(values)/len(values), 'record_ids':[r['record_id'] for r in rows],
            'notice':'原文报告标签的描述统计；同一来源内分别保留组成、温度、ΔE、方法、口径、符号和已知协议。未知协议不证明同协议，不是独立实验数量或性能排名。'})
    return result


def discover(request, repository):
    records = repository.records()
    result = original.discover(request, repository).to_dict()
    candidates = {i['evidence']['record_id']:i for i in result['candidates']}
    evidence, excluded = [], []
    for r in records:
        failures = unmet_constraints(r, request)
        auxiliary = auxiliary_match(r, request)
        if failures and not auxiliary:
            candidates.pop(r.record_id, None)
            excluded.append({'record_id':r.record_id,'unmet_constraints':failures,'quality_flags':r.quality_flags})
            continue
        scope = 'amplitude_only_path_unconfirmed' if auxiliary else 'requested_conditions'
        if r.record_id in candidates and not failures:
            candidates[r.record_id]['match_scope'] = scope
            continue
        evidence.append({'status':'amplitude_match_path_unconfirmed' if auxiliary else
            'source_reported_data' if usable_reported_label(r) else 'evidence_only_incomplete_protocol',
            'evidence':r.to_dict(), 'predictions':[], 'match_scope':scope,
            'unmet_constraints':failures, 'delta_E_kV_cm':amplitude(r),
            'warning':'幅度匹配、路径未确认；不证明指定路径下的实测性能。' if auxiliary else
                '有效原文报告数据；路径不完整不妨碍ΔE查询与原文标签统计。原始口径、符号和方法保留，完整协议或候选性能未据此确认。'})
    # Keep physical quantities separate even when the older personal domain
    # groups solely by endpoint/method. No scientific-source module is mutated.
    groups, keys = [], {}
    for item in candidates.values():
        r = item['evidence']
        key = tuple(r.get(k) for k in ('material_family','sample_form','temperature_K',
            'e_start_kV_cm','e_end_kV_cm','field_path','measurement_method','delta_t_basis','delta_t_sign_convention',
            'e_delta_kV_cm','e_max_kV_cm')) + ((r.get('raw_metadata_json') or {}).get('protocol_fingerprint'),)
        if key not in keys:
            keys[key] = {'group_id':f'protocol-{len(groups)+1}',
                         'conditions':{k:r.get(k) for k in ('material_family','sample_form','temperature_K',
                          'e_start_kV_cm','e_end_kV_cm','field_path','measurement_method','delta_t_basis','delta_t_sign_convention')},
                         'candidate_ids':[]}
            groups.append(keys[key])
        group = keys[key]
        group['candidate_ids'].append(r['record_id'])
        item.update(conditions=group['conditions'], ranking_context=group['group_id'])
    result.update(candidates=list(candidates.values()),groups=groups,evidence_only=evidence,excluded=excluded,
        descriptive_statistics=descriptive_statistics(list(candidates.values())+evidence),
        match_counts=match_counts(records,request),
        message=f'条件匹配原文记录 {len(candidates)+sum(i["match_scope"]=="requested_conditions" for i in evidence)} 条；'
                f'幅度匹配、路径未确认辅助证据 {sum(i["match_scope"]!="requested_conditions" for i in evidence)} 条。缺端点不删除原文标签；没有生成候选性能或路径验证结论。')
    return result


def enrich_seed_coverage(result, records, request):
    """Retain original seed-selection/chemistry rules; add amplitude scope counts."""
    from ecalx.materials.chemistry import exact_key, parse_formula
    by_composition = defaultdict(list)
    for r in records:
        if not (r.doi or r.url) or 'quarantined' in r.quality_flags:
            continue
        try:
            by_composition[exact_key(parse_formula(r.formula_normalized or r.formula_raw))].append(r)
        except ValueError:
            continue
    by_seed = {}
    by_id = {r.record_id:r for r in records}
    for seed in result['seeds']:
        original_seed = by_id[seed['seed_id']]
        rows = by_composition[exact_key(parse_formula(original_seed.formula_normalized or original_seed.formula_raw))]
        seed['coverage'].update(match_counts(rows, request))
        by_seed[seed['seed_id']] = seed['coverage']
    for candidate in result['candidates']:
        candidate['design_basis']['seed_coverage'] = by_seed[candidate['seed_id']]
        candidate['research_priority']['basis']['seed_experimental_coverage'] = by_seed[candidate['seed_id']]
    return result
