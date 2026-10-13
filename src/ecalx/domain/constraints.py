from dataclasses import asdict, dataclass, field


class ValidationError(ValueError):
    """An invalid or conflicting request, safe to display to the user."""


@dataclass(frozen=True)
class DiscoveryRequest:
    temperature_min_K: float | None = None
    temperature_max_K: float | None = None
    e_start_kV_cm: float | None = None
    e_end_kV_cm: float | None = None
    e_min_kV_cm: float | None = None
    e_max_kV_cm: float | None = None
    delta_E_kV_cm: float | None = None
    delta_t_operator: str | None = None
    delta_t_K: float | None = None
    maximize: bool = False
    material_type: str | None = None
    include_elements: tuple[str, ...] = ()
    exclude_elements: tuple[str, ...] = ()
    natural_language: str = ""
    raw_inputs: dict = field(default_factory=dict)
    confirmed_ambiguities: tuple[str, ...] = ()

    def __post_init__(self):
        import math
        from ecalx.materials.formulas import validate_symbols

        for name in ("temperature_min_K", "temperature_max_K", "e_start_kV_cm", "e_end_kV_cm",
                     "e_min_kV_cm", "e_max_kV_cm", "delta_E_kV_cm", "delta_t_K"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValidationError(name + " 必须为有限实数")
            if value is not None and name.startswith("temperature_") and value <= 0:
                raise ValidationError("温度必须高于绝对零度")
            if value is not None and (name.startswith("e_") or name == "delta_E_kV_cm") and value < 0:
                raise ValidationError("电场幅值不能为负数")
        for lo, hi in ((self.temperature_min_K, self.temperature_max_K), (self.e_min_kV_cm, self.e_max_kV_cm)):
            if lo is not None and hi is not None and lo > hi:
                raise ValidationError("区间上下限反向")
        for value in (self.e_start_kV_cm, self.e_end_kV_cm):
            if value is not None and ((self.e_min_kV_cm is not None and value < self.e_min_kV_cm)
                                      or (self.e_max_kV_cm is not None and value > self.e_max_kV_cm)):
                raise ValidationError("电场路径与范围冲突")
        if self.delta_E_kV_cm is not None and self.e_start_kV_cm is not None and self.e_end_kV_cm is not None:
            if not math.isclose(abs(self.e_end_kV_cm-self.e_start_kV_cm), self.delta_E_kV_cm, abs_tol=1e-9):
                raise ValidationError("电场变化幅度与路径冲突")
        if (self.delta_t_K is None) != (self.delta_t_operator is None) or self.delta_t_operator not in {None, ">", ">=", "<", "<=", "="}:
            raise ValidationError("ΔT 阈值和有效比较方向必须同时指定")
        validate_symbols(self.include_elements)
        validate_symbols(self.exclude_elements)
        if set(self.include_elements) & set(self.exclude_elements):
            raise ValidationError("元素包含与排除冲突")

    def to_dict(self):
        return asdict(self)

    @property
    def open_exploration(self):
        return not any((
            self.temperature_min_K is not None, self.temperature_max_K is not None,
            self.e_start_kV_cm is not None, self.e_end_kV_cm is not None,
            self.e_min_kV_cm is not None, self.e_max_kV_cm is not None,
            self.delta_E_kV_cm is not None,
            self.delta_t_K is not None, self.maximize, self.material_type,
            self.include_elements, self.exclude_elements,
        ))

    def unspecified(self):
        return [name for name, present in {
            "温度下限": self.temperature_min_K is not None,
            "温度上限": self.temperature_max_K is not None,
            "电场起点": self.e_start_kV_cm is not None,
            "电场终点": self.e_end_kV_cm is not None,
            "电场下限": self.e_min_kV_cm is not None,
            "电场上限": self.e_max_kV_cm is not None,
            "电场变化幅度": self.delta_E_kV_cm is not None,
            "ΔT 阈值": self.delta_t_K is not None,
            "材料类型": bool(self.material_type),
            "必须包含元素": bool(self.include_elements),
            "排除元素": bool(self.exclude_elements),
        }.items() if not present]


@dataclass(frozen=True)
class ParseResult:
    request: DiscoveryRequest
    pending: tuple[str, ...] = ()
    requires_confirmation: bool = False

    def confirm(self, accept_unresolved=False):
        if self.pending and not accept_unresolved:
            raise ValidationError("存在待确认语义；请补充数值，或明确确认这些文本暂不作为筛选条件。")
        from dataclasses import replace
        return replace(self.request, confirmed_ambiguities=self.pending)
