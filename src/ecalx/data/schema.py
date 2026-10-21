from dataclasses import asdict, dataclass, field

from ecalx.domain.constraints import ValidationError
from ecalx.materials.formulas import formula_elements
from ecalx.parsing.units import finite_number

EXPERIMENTAL_RESPONSES = {
    "delta_T_jump_abs_K": ("器件温跃幅值 |ΔTj|", "K"),
    "delta_S_vol_abs_kJ_K_m3": ("体积熵变幅值 |ΔS|", "kJ/(K·m³)"),
}
ADDED_FIELDS = tuple(EXPERIMENTAL_RESPONSES) + (
    "target_kind", "value_derivation", "normalization_basis", "field_level_role",
    "source_workbook", "source_sha256", "source_sheet", "temperature_cell", "response_cell")


@dataclass
class EvidenceRecord:
    record_id: str
    formula_raw: str
    source_id: str | None = None
    doi: str | None = None
    url: str | None = None
    original_row: str | None = None
    formula_normalized: str | None = None
    material_id: str | None = None
    specimen_id: str | None = None
    material_family: str | None = None
    sample_form: str | None = None
    processing_json: dict | None = None
    temperature_K: float | None = None
    e_start_kV_cm: float | None = None
    e_end_kV_cm: float | None = None
    e_delta_kV_cm: float | None = None
    e_max_kV_cm: float | None = None
    # Source scalar is deliberately separate from path endpoints.
    e_reported_kV_cm: float | None = None
    e_reported_kind: str = "unknown"
    figure_id: str | None = None
    composition_key: str | None = None
    delta_t_basis: str = "reported_delta_t"
    delta_t_sign_convention: str = "source_reported"
    field_path: str = "unknown"
    measurement_method: str = "unknown"
    delta_T_ad_K: float | None = None
    delta_S_J_kgK: float | None = None
    Tc_K: float | None = None
    Cp_J_kgK: float | None = None
    density_kg_m3: float | None = None
    quality_flags: list[str] = field(default_factory=list)
    raw_metadata_json: dict = field(default_factory=dict)
    import_id: str | None = None
    # Independent observed quantities; never mapped into intrinsic/mass labels.
    delta_T_jump_abs_K: float | None = None
    delta_S_vol_abs_kJ_K_m3: float | None = None
    target_kind: str | None = None
    value_derivation: str | None = None
    normalization_basis: str | None = None
    field_level_role: str | None = None
    source_workbook: str | None = None
    source_sha256: str | None = None
    source_sheet: str | None = None
    temperature_cell: str | None = None
    response_cell: str | None = None

    def validate(self):
        if not self.record_id.strip() or not self.formula_raw.strip():
            raise ValidationError("每条记录必须有 record_id 与 formula_raw")
        if self.measurement_method not in {"direct", "indirect", "unknown"}:
            raise ValidationError("measurement_method 必须为 direct / indirect / unknown")
        if self.field_path not in {"loading", "unloading", "unknown"}:
            raise ValidationError("field_path 必须为 loading / unloading / unknown")
        if self.e_reported_kind not in {"delta", "maximum", "reported", "unknown"}:
            raise ValidationError("e_reported_kind 必须为 delta / maximum / reported / unknown")
        for name in ("temperature_K", "e_start_kV_cm", "e_end_kV_cm", "e_reported_kV_cm", "e_delta_kV_cm", "e_max_kV_cm", "delta_T_ad_K",
                     "delta_S_J_kgK", "Tc_K", "Cp_J_kgK", "density_kg_m3", *EXPERIMENTAL_RESPONSES):
            value = getattr(self, name)
            if value is not None:
                value = finite_number(value, name)
                if name in {"temperature_K", "Tc_K", "Cp_J_kgK", "density_kg_m3"} and value <= 0:
                    raise ValidationError(f"{name} 必须大于 0")
                if name.startswith("e_") and value < 0:
                    raise ValidationError("电场幅值不能为负数")
                if name in EXPERIMENTAL_RESPONSES and value < 0:
                    raise ValidationError(name + " 为幅值，不能为负数")
                setattr(self, name, value)
        if self.experimental_response:
            if self.delta_T_ad_K is not None or self.delta_S_J_kgK is not None:
                raise ValidationError("器件温跃/体积熵变须单独记录，不能同时填入本征温变/质量熵变")
            if all(getattr(self, k) is not None for k in EXPERIMENTAL_RESPONSES):
                raise ValidationError("两类响应须按各自器件与观测记录分行保存")
        if self.e_start_kV_cm is not None and self.e_end_kV_cm is not None:
            if (self.field_path == "loading" and self.e_start_kV_cm > self.e_end_kV_cm) or (
                self.field_path == "unloading" and self.e_start_kV_cm < self.e_end_kV_cm
            ):
                raise ValidationError("field_path 与电场起止方向矛盾")
        if not isinstance(self.quality_flags, list) or not all(isinstance(x, str) for x in self.quality_flags):
            raise ValidationError("quality_flags 必须为字符串数组")
        if not isinstance(self.raw_metadata_json, dict) or (self.processing_json is not None and not isinstance(self.processing_json, dict)):
            raise ValidationError("metadata / processing 必须为 JSON 对象")
        flags = []
        if not (self.source_id and (self.doi or self.url) and self.original_row):
            flags.append("incomplete_source")
        if formula_elements(self.formula_normalized or self.formula_raw) is None:
            flags.append("unparsed_formula")
        if any(getattr(self, name) is None for name in ("temperature_K", "e_start_kV_cm", "e_end_kV_cm", "delta_T_ad_K")):
            flags.append("incomplete_conditions")
        if self.measurement_method == "unknown" or self.field_path == "unknown" or not self.sample_form:
            flags.append("incomplete_protocol")
        self.quality_flags = sorted(set(self.quality_flags + flags))
        return self

    def to_dict(self):
        result = asdict(self)
        # Legacy payloads and their data versions remain byte-content equivalent.
        for name in ADDED_FIELDS:
            if result[name] is None:
                result.pop(name)
        return result

    @property
    def experimental_response(self):
        return any(getattr(self, name) is not None for name in EXPERIMENTAL_RESPONSES)

    @property
    def recommendable(self):
        return not self.experimental_response and not set(self.quality_flags) & {"incomplete_source", "incomplete_conditions", "incomplete_protocol", "unparsed_formula", "sign_convention_unverified", "quarantined"}
