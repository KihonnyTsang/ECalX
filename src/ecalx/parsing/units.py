import math
import re

from ecalx.domain.constraints import ValidationError

NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def finite_number(value, label="数值"):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{label}必须是有效实数：{value}") from None
    if not math.isfinite(result):
        raise ValidationError(f"{label}不能是 NaN 或无穷大")
    return result


def temperature_K(value, unit="°C"):
    if value is None or str(value).strip() == "":
        return None
    match = re.fullmatch(rf"\s*({NUMBER})\s*(°?C|℃|K)?\s*", str(value))
    if not match:
        raise ValidationError(f"无法解析温度：{value}；请使用 °C 或 K")
    number = finite_number(match[1], "温度")
    unit = match[2] or unit
    if unit in ("C", "°C", "℃"):
        number += 273.15
    elif unit != "K":
        raise ValidationError(f"未知温度单位：{unit}")
    if number <= 0:
        raise ValidationError("温度必须高于绝对零度（0 K）")
    return number


def field_kV_cm(value, unit="kV/cm"):
    if value is None or str(value).strip() == "":
        return None
    if unit not in {"kV/cm", "kV/mm", "MV/m"}:
        raise ValidationError(f"未知电场单位：{unit}；电压不能直接换算为场强")
    number = finite_number(value, "电场")
    if number < 0:
        raise ValidationError("电场幅值不能为负数；撤场请使用起点→终点")
    converted = number * {"kV/cm": 1, "kV/mm": 10, "MV/m": 10}[unit]
    return finite_number(converted, "换算后的电场")
