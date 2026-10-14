"""Evidence-only inverse lookup. Missing protocols never become comparable by accident."""
import math
import operator
from collections import Counter
from dataclasses import asdict, dataclass, field

from ecalx.domain.constraints import DiscoveryRequest
from ecalx.materials.formulas import formula_elements

MODEL_STATE = "NOT_TRAINED"
COMPARATORS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "=": operator.eq}
FORM_ALIASES = {"陶瓷": "bulk ceramic", "薄膜": "thin film", "聚合物": "polymer"}


def unmet_constraints(record, request):
    unmet = []
    if request.delta_E_kV_cm is not None:
        amplitude = (abs(record.e_end_kV_cm-record.e_start_kV_cm)
                     if record.e_start_kV_cm is not None and record.e_end_kV_cm is not None
                     else record.e_delta_kV_cm if record.e_delta_kV_cm is not None
                     else record.e_reported_kV_cm if record.e_reported_kind == 'delta' else None)
        if amplitude is None or not math.isclose(amplitude, request.delta_E_kV_cm, abs_tol=1e-9):
            unmet.append('电场变化幅度')
    for name, bound, comparator in (
        ("temperature_K", request.temperature_min_K, operator.ge),
        ("temperature_K", request.temperature_max_K, operator.le),
        ("delta_T_ad_K", request.delta_t_K, COMPARATORS.get(request.delta_t_operator, operator.ge)),
    ):
        value = getattr(record, name)
        if name == "delta_T_ad_K" and "sign_convention_unverified" in record.quality_flags:
            value = None
        if bound is not None and (value is None or not comparator(value, bound)):
            unmet.append(name)
    for name, expected in (("e_start_kV_cm", request.e_start_kV_cm), ("e_end_kV_cm", request.e_end_kV_cm)):
        actual = getattr(record, name)
        if expected is not None and (actual is None or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)):
            unmet.append(name)
    for name in ("e_start_kV_cm", "e_end_kV_cm"):
        actual = getattr(record, name)
        if request.e_min_kV_cm is not None and (actual is None or actual < request.e_min_kV_cm):
            unmet.append("电场下限")
        if request.e_max_kV_cm is not None and (actual is None or actual > request.e_max_kV_cm):
            unmet.append("电场上限")
    if request.material_type:
        wanted = FORM_ALIASES.get(request.material_type, request.material_type).casefold()
        # Exact free-text match only; do not silently coerce a polymer film into a ceramic.
        if not record.sample_form or record.sample_form.casefold() != wanted:
            unmet.append("材料类型")
    if request.include_elements or request.exclude_elements:
        elements = formula_elements(record.formula_normalized or record.formula_raw)
        if elements is None:
            unmet.append("组成无法可靠解析")
        else:
            if set(request.include_elements) - elements:
                unmet.append("必须包含元素")
            if set(request.exclude_elements) & elements:
                unmet.append("排除元素")
    return sorted(set(unmet))


def comparable_key(record):
    # Exact temperature point, family, form, direction, and fidelity; no tolerance assumed.
    return (record.material_family, record.sample_form, record.temperature_K,
            record.e_start_kV_cm, record.e_end_kV_cm, record.field_path, record.measurement_method,
            record.delta_t_basis, record.delta_t_sign_convention,
            record.raw_metadata_json.get('protocol_fingerprint'),
            tuple(k for k in ('delta_T_ad_K','delta_S_J_kgK') if getattr(record,k) is not None))


def coverage(records):
    def counts(name):
        return dict(Counter(str(getattr(r, name) if getattr(r, name) is not None else "未知") for r in records))
    temperatures = [r.temperature_K for r in records if r.temperature_K is not None]
    return {"records": len(records), "material_ids": len({r.material_id for r in records if r.material_id}),
            "specimen_ids": len({r.specimen_id for r in records if r.specimen_id}),
            "formula_strings": len({r.formula_normalized or r.formula_raw for r in records}),
            "material_family": counts("material_family"), "sample_form": counts("sample_form"),
            "measurement_method": counts("measurement_method"), "field_start_kV_cm": counts("e_start_kV_cm"),
            "field_end_kV_cm": counts("e_end_kV_cm"),
            "reported_field_kind": counts("e_reported_kind"),
            "reported_field_kV_cm": ([min(r.e_reported_kV_cm for r in records if r.e_reported_kV_cm is not None),
                                      max(r.e_reported_kV_cm for r in records if r.e_reported_kV_cm is not None)]
                                     if any(r.e_reported_kV_cm is not None for r in records) else None),
            "protocol_complete_records": sum(r.recommendable for r in records),
            "temperature_K": [min(temperatures), max(temperatures)] if temperatures else None,
            "quality_flags": dict(Counter(flag for r in records for flag in r.quality_flags))}


@dataclass
class DiscoveryReport:
    request: dict
    data_version: str
    candidates: list
    groups: list
    coverage: dict
    excluded: list
    message: str
    evidence_only: list = field(default_factory=list)
    model_state: str = MODEL_STATE
    model_version: str | None = None
    ranking_basis: str = "仅按相同温度/场路径/方法/形态/家族分组；组间不排名，组内按记录 ID 展示"

    def to_dict(self):
        return asdict(self)


def discover(request: DiscoveryRequest, repository):
    records = repository.records()
    data_version = repository.data_version
    candidates, groups, by_key, excluded, evidence_only = [], [], {}, [], []
    for record in records:
        unmet = unmet_constraints(record, request)
        if unmet or not record.recommendable:
            excluded.append({"record_id": record.record_id, "unmet_constraints": unmet,
                             "quality_flags": record.quality_flags})
            if not unmet:
                evidence_only.append({"status": "evidence_only_incomplete_protocol", "evidence": record.to_dict(),
                                      "predictions": [], "warning": "协议/来源待核验；不构成满足完整科研目标的推荐"})
            continue
        key = comparable_key(record)
        if key not in by_key:
            group = {"group_id": f"protocol-{len(groups) + 1}", "conditions": {
                "material_family": key[0], "sample_form": key[1], "temperature_K": key[2],
                "e_start_kV_cm": key[3], "e_end_kV_cm": key[4], "field_path": key[5],
                "measurement_method": key[6]}, "candidate_ids": []}
            by_key[key] = group
            groups.append(group)
        group = by_key[key]
        group["candidate_ids"].append(record.record_id)
        candidates.append({"candidate_id": record.record_id, "formula": record.formula_normalized or record.formula_raw,
                           "material_family": record.material_family, "sample_form": record.sample_form,
                           "status": "measured", "evidence_origin": "imported_experimental_record",
                           "evidence": record.to_dict(), "conditions": group["conditions"],
                           "measurements": {"delta_T_ad_K": record.delta_T_ad_K, "method": record.measurement_method},
                           "predictions": [], "domain_warning": "无可用预测模型；仅为记录测试点，不代表整个温区满足目标",
                           "matched_constraints": [k for k, v in request.to_dict().items() if v is not None and k not in {"raw_inputs", "natural_language", "confirmed_ambiguities"} and v != () and v is not False],
                           "unmet_constraints": [], "ranking_context": group["group_id"],
                           "model_version": None, "data_version": data_version,
                           "research_advice": "核查原始来源、样品和实验协议；如需目标温区响应，应补充目标条件测量。"})
    message = "已按可比较实验协议展示证据；尚无可用预测模型。" if candidates else (
        "数据尚未导入。请在材料证据库导入有来源及使用许可的 CSV。" if not records else "未发现可信满足项；请查看排除原因或补充实验数据。")
    if evidence_only and not candidates:
        message += f" 有 {len(evidence_only)} 条已知字段匹配的原始证据，实验协议不完整，仅供核查。"
    return DiscoveryReport(request.to_dict(), data_version, candidates, groups, coverage(records), excluded, message,
                           evidence_only=evidence_only)
