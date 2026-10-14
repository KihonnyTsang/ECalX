"""Receipt-time condition counts. No inferred endpoints or candidate properties."""
from collections import Counter
from dataclasses import replace
import hashlib
import json

from ecalx.domain.constraints import DiscoveryRequest
from .evidence_matching import unmet_constraints, match_counts

TEMPERATURE = ('temperature_min_K', 'temperature_max_K')
FIELD = ('e_start_kV_cm', 'e_end_kV_cm', 'e_min_kV_cm', 'e_max_kV_cm', 'delta_E_kV_cm')
ZERO_NOTICE = '目标条件下无匹配实验依据；候选仅为 generated_unverified 研究假设，需要补测，不能判断已达标。'
SEMANTICS = '电场上限只核对已知起止端点均不超过上限；ΔE只核对变化幅度；明确路径逐端点核对，只有ΔE的记录可作为幅度匹配、路径未确认的辅助证据。缺端点不妨碍ΔE查询；不能用ΔE补齐端点，未输入的条件保持未指定。'


class EvidenceSnapshot:
    def __init__(self, records):
        self._records = records
        raw = json.dumps([r.to_dict() for r in records], sort_keys=True, ensure_ascii=False)
        self.data_version = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()

    def records(self):
        return self._records


def condition_summary(records, request, data_version):
    """T-only/E-only/joint use the full library; all-goal counts also apply other goals."""
    tr = DiscoveryRequest(**{k: getattr(request, k) for k in TEMPERATURE})
    er = DiscoveryRequest(**{k: getattr(request, k) for k in FIELD})
    tj = any(getattr(request, k) is not None for k in TEMPERATURE)
    ej = any(getattr(request, k) is not None for k in FIELD)
    t_hits = [not unmet_constraints(r, tr) for r in records]
    e_hits = [not unmet_constraints(r, er) for r in records]
    matched = [r for r in records if not unmet_constraints(r, request)]
    reasons = Counter(reason for r in records for reason in unmet_constraints(r, request))
    flags = Counter(flag for r in matched if not r.recommendable for flag in r.quality_flags)
    return {'schema_version': 'local-condition-evidence-v1', 'data_version': data_version,
        **match_counts(records, request), 'library_records': len(records), 'temperature_requested': tj, 'field_requested': ej,
        'temperature_matches': sum(t_hits) if tj else None,
        'field_matches': sum(e_hits) if ej else None,
        'joint_matches': sum(t and e for t, e in zip(t_hits, e_hits)) if tj or ej else None,
        'all_goal_matches': len(matched),
        'complete_protocol_matches': sum(r.recommendable for r in matched),
        'incomplete_protocol_matches': sum(not r.recommendable for r in matched),
        'mismatch_reasons': dict(reasons), 'protocol_gaps': dict(flags),
        'missing_field_endpoints': sum(r.e_start_kV_cm is None or r.e_end_kV_cm is None for r in records),
        'missing_temperature': sum(r.temperature_K is None for r in records),
        'scope': '全库逐记录计数；T和E单项不应用其他目标，联合只应用已指定的T/E；全部条件匹配另列。记录不是独立实验，点命中不保证整个温区或候选性能。',
        'semantics': SEMANTICS}


def protocol_evidence(root, records, request):
    """Separate local-only view, validated against ledger, source and PDF SHA.

    Rebuild overlays in memory with the original verifier; never call its writers.
    Failure hides the entire view instead of silently trusting stored Q3 flags.
    """
    from ecalx.data.protocol import build_overlays
    try:
        old_path = root / 'docs/protocol_review.json'
        new_path = root / 'docs/protocol_review_v2.json'
        overlay_path = root / 'data/processed/protocol_overlays_v2.json'
        ledger = json.loads(old_path.read_text())
        new = json.loads(new_path.read_text())
        ledger['evidence'].update(new['additional_evidence'])
        ledger['papers'] += new['additional_A_papers']
        ledger['row_reviews'] += new['additional_A_row_reviews']
        # Verify actual locked workbook bytes, not just metadata claims.
        manifest = json.loads((root / 'data/source_manifest.json').read_text())['A']
        for entry in manifest['files']:
            if hashlib.sha256((root / entry['path']).read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('source changed')
        # The overlay was built from the locked workbooks. The SQLite carries
        # additional audit flags; don't mistake those for changed protocol facts.
        from ecalx.data.research_sources import adapt_a
        original_records = [r for method in ('direct', 'indirect')
                            for r in adapt_a(root, manifest, method).records]
        rebuilt = build_overlays(original_records, ledger, root)
        stored = json.loads(overlay_path.read_text())
        if stored != rebuilt:
            raise ValueError('overlay changed')
        source_rows = {r.record_id: r for r in original_records}
        rows = []
        for record in records:
            review = rebuilt[record.record_id]
            if review['quality_grade'] != 'Q3':
                continue
            facts = review['verified_fields']
            if facts['sign_convention'] != 'positive_temperature_drop_magnitude':
                raise ValueError('unsupported observable')
            original = source_rows[record.record_id]
            for key in ('formula_normalized', 'temperature_K', 'e_reported_kV_cm', 'delta_T_ad_K', 'original_row', 'doi'):
                if getattr(original, key) != getattr(record, key):
                    raise ValueError('source value differs')
            checked = replace(record, e_start_kV_cm=facts['e_start_kV_cm'],
                e_end_kV_cm=facts['e_end_kV_cm'], field_path=facts['field_path'])
            rows.append({'record_id': record.record_id, 'formula': record.formula_normalized,
                'doi': record.doi, 'temperature_K': record.temperature_K,
                'positive_drop_magnitude_K': record.delta_T_ad_K,
                'signed_delta_T_K': None, 'verified_fields': facts,
                'unmet_conditions': unmet_constraints(checked, request),
                'remaining_gaps': review['review_quality_flags'],
                'original_row': record.original_row,
                'evidence': [ledger['evidence'][eid] for eid in review['evidence_ids']]})
        return {'status': 'verified_readonly', 'records': rows,
            'papers': len({r['doi'] for r in rows}),
            'matching_records': sum(not r['unmet_conditions'] for r in rows),
            'hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (old_path, new_path, overlay_path)},
            'notice': f'独立于默认库的协议证据区：{len(rows)}条记录来自{len({r["doi"] for r in rows})}篇论文、已曝光开发标签，并非多项独立验证或跨论文验证。303 K、50→0 kV/cm、T型热电偶正温降幅值；不是通用带符号ΔT。样品身份和独立批次仍未知；M5禁用。'}
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'records': [],
                'notice': '协议覆盖层、源文件或核验指纹无法一致核对，未展示协议数值；未改写原库。'}


def receipt_conditions(task):
    calls = [c for c in task['calls'] if c['name'] == 'search_material_evidence'
             and c['output']['status'] == 'OK']
    return calls[-1]['output']['data'].get('condition_evidence') if calls else None


def coverage_text(coverage):
    def count(key):
        v = coverage.get(key)
        return '未指定' if v is None else f'{v} 条'
    return (f"种子共 {coverage.get('records', '未知')} 条记录；温度命中 {count('temperature_point_hits')}；"
            f"电场命中 {count('field_fact_hits')}；温度/电场联合命中 {count('joint_temperature_field_hits')}。"
            + (f" ΔE幅度命中 {count('delta_E_matches')}；其中路径未知 {count('delta_E_matches_path_unknown')}；辅助证据 {count('amplitude_only_auxiliary_matches')}。" if 'delta_E_matches' in coverage else ''))


REASONS = {'temperature_K': '温度缺失或不在指定范围', '电场上限': '起止端点缺失或超过电场上限',
    '电场下限': '起止端点缺失或低于电场下限', '电场变化幅度': 'ΔE未知或幅度不符',
    'e_start_kV_cm': '起点缺失或不符', 'e_end_kV_cm': '终点缺失或不符',
    'delta_T_ad_K': 'ΔT数值/符号未核验或不满足目标',
    'field_endpoints_unknown': '电场端点未知', 'figure_missing': '图表定位缺失',
    'incomplete_conditions': '条件不完整', 'incomplete_protocol': '协议不完整',
    'nominal_ABO3_oxygen': '氧计量为名义ABO3', 'specimen_unknown': '样品身份未知'}


def summary_lines(summary):
    if summary is None:
        return ['本任务未保存完整条件计数快照；历史收据保持原样，未用当前库回填。']
    def count(key):
        return '未指定' if summary[key] is None else f'{summary[key]} 条'
    lines = [f"本次文献库：{summary['library_records']} 条记录。",
        f"符合温度：{count('temperature_matches')}；符合电场：{count('field_matches')}；温度/电场联合：{count('joint_matches')}。",
        f"全部已指定条件匹配：{summary['all_goal_matches']} 条；其中完整协议 {summary['complete_protocol_matches']} 条、协议不完整 {summary['incomplete_protocol_matches']} 条。",
        summary['scope'], summary['semantics']]
    if 'delta_E_matches' in summary:
        lines += amplitude_summary_lines(summary)
    if summary['all_goal_matches'] == 0:
        lines += [evidence_notice(summary)]
    lines += [f"缺少温度：{summary['missing_temperature']} 条；缺少一个或两个场端点：{summary['missing_field_endpoints']} 条。"]
    lines += summary.get('field_reference_notice', [])
    for label, key in [('无法匹配原因', 'mismatch_reasons'), ('匹配记录的协议缺口', 'protocol_gaps')]:
        lines += [label + '（同一记录可有多项原因，不相加）：' +
                  ('；'.join(f'{REASONS.get(k,k)} {v} 条' for k,v in summary[key].items()) or '无')]
    return lines


def amplitude_summary_lines(summary):
    if 'delta_E_matches' not in summary:
        return []
    def count(key):
        v=summary.get(key)
        return '未指定' if v is None else f'{v} 条'
    return [f"ΔE幅度匹配：{count('delta_E_matches')}；其中路径未知：{count('delta_E_matches_path_unknown')}；符合温度/组成等条件的幅度匹配：{count('delta_E_context_matches')}。",
        f"完整路径已核实记录：{count('verified_path_records')}；核实路径且符合全部条件：{count('verified_path_matches')}；路径未知记录：{count('path_unknown_records')}。",
        f"有组成/温度/ΔE/原文ΔT标签的条件匹配记录：{count('reported_label_matches')}；幅度匹配、路径未确认辅助证据：{count('amplitude_only_auxiliary_matches')}。",
        summary['amplitude_count_scope'], summary['verified_path_scope']]


def evidence_notice(summary):
    verified=summary.get('verified_path_matches',0) if summary else 0
    if verified:
        return f'默认库没有严格端点匹配；独立已核实路径证据区有 {verified} 条符合条件的记录，原始端点字段保持原样。候选性能仍未知。'
    auxiliary=summary.get('amplitude_only_auxiliary_matches',0) if summary else 0
    if auxiliary:
        return f'无严格路径匹配；有 {auxiliary} 条幅度匹配、路径未确认的辅助证据，不能据此声称指定路径下的实测性能。候选性能仍未知。'
    return ZERO_NOTICE
