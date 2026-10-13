"""Deterministic rules. Unrecognized language is retained for confirmation."""
import math
import re

from ecalx.domain.constraints import DiscoveryRequest, ParseResult, ValidationError
from ecalx.materials.formulas import validate_symbols
from ecalx.parsing.units import NUMBER, field_kV_cm, finite_number, temperature_K

TEMP_UNIT = r"(?:°C|℃|C|K)"
FIELD_UNIT = r"(?:kV/cm|kV/mm|MV/m)"
SEP = r"(?:–|—|~|～|至|到|-)"
FIELD_PATTERN = rf"(?:(?:最大|上限|不超过|<=|≤)\s*{NUMBER}|{NUMBER}\s*(?:→|->|{SEP})\s*{NUMBER})\s*{FIELD_UNIT}"
AMPLITUDE_PATTERN = rf"(?:ΔE|变化幅度)\s*(?:=|:|：)?\s*({NUMBER})\s*({FIELD_UNIT})"
DELTA_PATTERN = rf"(?:ΔT|delta_?[tT]|温变)\s*(?:>=|<=|>|<|=|≥|≤)\s*{NUMBER}\s*K"
ELEMENT_PATTERN = r"(?:必须含|必须包含|包含|排除|不含|含)\s*[A-Z][a-z]?(?:\s*[,、/和]\s*[A-Z][a-z]?)*"
SOFT_PREFIX = r"(?:优先|最好|尽可能|偏好|希望|不必|无需|可以|允许|建议|如果|若|不要求|不一定|不要)\s*$"


def parse_field(text):
    text = text.strip()
    if not text:
        return {}
    amplitude = re.fullmatch(AMPLITUDE_PATTERN, text)
    if amplitude:
        return {"delta_E_kV_cm": field_kV_cm(amplitude[1], amplitude[2])}
    cap = re.fullmatch(rf"(?:最大|上限|不超过|<=|≤)\s*({NUMBER})\s*({FIELD_UNIT})", text)
    if cap:
        return {"e_max_kV_cm": field_kV_cm(cap[1], cap[2])}
    path = re.fullmatch(rf"({NUMBER})\s*(→|->|{SEP})\s*({NUMBER})\s*({FIELD_UNIT})", text)
    if path:
        start, end = field_kV_cm(path[1], path[4]), field_kV_cm(path[3], path[4])
        if path[2] in ("→", "->"):
            return {"e_start_kV_cm": start, "e_end_kV_cm": end}
        if start > end:
            raise ValidationError("电场范围上下限反向；撤场路径请用 →")
        return {"e_min_kV_cm": start, "e_max_kV_cm": end}
    raise ValidationError("无法解析电场。示例（非默认）：0→2 kV/mm、最大 3 MV/m、10–30 kV/cm")


def parse_delta(text):
    if not text.strip():
        return {}
    if text.strip() in ("尽可能大", "尽可能高", "maximize"):
        return {"maximize": True}
    match = re.fullmatch(rf"\s*(?:ΔT|delta_?[tT]|温变)?\s*(>=|<=|>|<|=|≥|≤)\s*({NUMBER})\s*K\s*", text)
    if not match:
        raise ValidationError("无法解析 ΔT 目标。请填写如 ΔT >= 0.65 K、ΔT <= -0.2 K 或尽可能大")
    return {"delta_t_operator": {"≥": ">=", "≤": "<="}.get(match[1], match[1]),
            "delta_t_K": finite_number(match[2], "ΔT")}


def parse_elements(text):
    include, exclude = set(), set()
    residual = text
    for match in re.finditer(ELEMENT_PATTERN, text):
        phrase = match[0]
        if re.search(SOFT_PREFIX, text[:match.start()]):
            continue  # Retain a preference as pending language, never a hard exclusion.
        symbols = re.findall(r"[A-Z][a-z]?", phrase)
        validate_symbols(symbols)
        (exclude if phrase.startswith(("排除", "不含")) else include).update(symbols)
        residual = residual.replace(phrase, "", 1)
    for match in list(re.finditer("无铅", text)):
        if not re.search(SOFT_PREFIX, text[:match.start()]):
            exclude.add("Pb")
            residual = residual.replace("无铅", "", 1)
    return include, exclude, residual.strip(" ,，、；;。\n")


def _same(a, b):
    if isinstance(a, (float, int)) and isinstance(b, (float, int)):
        return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8)
    return a == b


def _merge(target, values):
    for name, value in values.items():
        if name in target and not _same(target[name], value):
            raise ValidationError(f"约束冲突：{name}，{target[name]} 与 {value}；请修正输入后重新解析")
        target[name] = value


def _natural(text):
    values, pending = {}, []
    remaining = text

    def consume(pattern, handler):
        nonlocal remaining
        original = remaining
        def replace(match):
            if re.search(SOFT_PREFIX, original[:match.start()]):
                return match[0]
            _merge(values, handler(match))
            return " "
        remaining = re.sub(pattern, replace, original)

    consume(rf"({NUMBER})\s*(?:{TEMP_UNIT})?\s*{SEP}\s*({NUMBER})\s*({TEMP_UNIT})",
            lambda m: {"temperature_min_K": temperature_K(m[1], m[3]), "temperature_max_K": temperature_K(m[2], m[3])})
    consume(rf"(?:最低温(?:度)?|温度下限|温度\s*(?:>=|≥))\s*({NUMBER})\s*({TEMP_UNIT})",
            lambda m: {"temperature_min_K": temperature_K(m[1], m[2])})
    consume(rf"(?:最高温(?:度)?|温度上限|温度\s*(?:<=|≤))\s*({NUMBER})\s*({TEMP_UNIT})",
            lambda m: {"temperature_max_K": temperature_K(m[1], m[2])})
    consume(AMPLITUDE_PATTERN, lambda m: parse_field(m[0]))
    consume(FIELD_PATTERN, lambda m: parse_field(m[0]))
    consume(DELTA_PATTERN, lambda m: parse_delta(m[0]))
    consume(r"尽可能大|尽可能高", lambda m: {"maximize": True})
    consume(r"(?:材料类型|材料形态)\s*[:：]\s*([^;；，,\n]+)", lambda m: {"material_type": m[1].strip()})
    if "material_type" not in values:
        consume(r"聚合物薄膜|聚合物|陶瓷|薄膜", lambda m: {"material_type": m[0]})
    include, exclude, remaining = parse_elements(remaining)
    if include:
        values["include_elements"] = tuple(sorted(include))
    if exclude:
        values["exclude_elements"] = tuple(sorted(exclude))
    for phrase in ("低电场", "室温附近", "稀有元素", "高温变", "平均温变高", "宽温区"):
        if phrase in remaining:
            pending.append(phrase + "：未转换成数值或筛选规则")
            remaining = remaining.replace(phrase, "")
    # Only remove grammatical wrappers; any scientific content stays visible.
    remaining = re.sub(r"只考虑|工作温区|温区|电场|请推荐|推荐|材料|要求|且|并且|希望|范围|在", "", remaining)
    remaining = remaining.strip(" ,，、；;。:：\n")
    if remaining:
        pending.append("未解析文本：" + remaining)
    return values, pending


def parse_request(raw=None):
    raw = dict(raw or {})
    values, pending = {}, []
    unit = raw.get("temperature_unit", "°C")
    for suffix in ("min", "max"):
        value = temperature_K(raw.get("temperature_" + suffix), unit)
        if value is not None:
            values[f"temperature_{suffix}_K"] = value
    field_unit = raw.get("field_unit", "kV/cm")
    for suffix in ("start", "end", "min", "max"):
        value = field_kV_cm(raw.get("e_" + suffix), field_unit)
        if value is not None:
            values[f"e_{suffix}_kV_cm"] = value
    _merge(values, parse_field(raw.get("electric_field", "")))
    _merge(values, parse_delta(raw.get("delta_t", "")))
    material = raw.get("material_type", "").strip()
    if material:
        values["material_type"] = re.sub(r"^材料类型\s*[:：]\s*", "", material)
    inc, exc, residual = parse_elements(raw.get("elements", ""))
    if residual:
        pending.append("元素/组成要求未解析：" + residual)
    text = raw.get("natural_language", "").strip()
    natural, natural_pending = _natural(text)
    inc.update(natural.pop("include_elements", ()))
    exc.update(natural.pop("exclude_elements", ()))
    _merge(values, natural)
    pending.extend(natural_pending)
    if inc & exc:
        raise ValidationError("元素约束冲突：同时包含与排除 " + ", ".join(sorted(inc & exc)))
    values.update(include_elements=tuple(sorted(inc)), exclude_elements=tuple(sorted(exc)))
    lo, hi = values.get("temperature_min_K"), values.get("temperature_max_K")
    if lo is not None and hi is not None and lo > hi:
        raise ValidationError("温度区间反向：下限必须小于或等于上限")
    lo, hi = values.get("e_min_kV_cm"), values.get("e_max_kV_cm")
    if lo is not None and hi is not None and lo > hi:
        raise ValidationError("电场区间反向：下限大于上限")
    for key in ("e_start_kV_cm", "e_end_kV_cm"):
        value = values.get(key)
        if value is not None and ((lo is not None and value < lo) or (hi is not None and value > hi)):
            raise ValidationError("电场路径与场强范围/上限冲突")
    return ParseResult(DiscoveryRequest(**values, natural_language=text, raw_inputs=raw),
                       tuple(pending), bool(text or pending))
