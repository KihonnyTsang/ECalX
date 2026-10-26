"""UI-only wording normalization; scientific parsing and stored facts stay intact."""
import re
from ecalx.parsing.units import NUMBER


def field_language(text):
    # These phrases explicitly describe a cap or a directed path, never ΔE.
    # Unitless field values on this kV/cm form are made visible in the review.
    unit = r'(kV/cm|kV/mm|MV/m)?'
    boundary = r'(?![\w./])'
    cap = rf'(?:最大(?:电场|场强)(?:\s*不超过|\s*为|\s*[≤=])?|(?:电场|场强)\s*不超过)\s*({NUMBER})\s*{unit}{boundary}'
    path = rf'从\s*({NUMBER})\s*(?:加到|升到|降到|撤到)\s*({NUMBER})\s*{unit}{boundary}'
    text = re.sub(cap, lambda m: f'最大 {m[1]} {m[2] or "kV/cm"}', text)
    return re.sub(path, lambda m: f'{m[1]}→{m[2]} {m[3] or "kV/cm"}', text)


def condition_lines(goals):
    def val(key, unit):
        value = goals[key]
        return '未指定' if value is None else f'{value:g} {unit}'
    lines = [f"温度范围：{val('temperature_min_K', 'K')} 至 {val('temperature_max_K', 'K')}",
             f"电场变化幅度 ΔE：{val('delta_E_kV_cm', 'kV/cm')}",
             '排除元素：' + ('、'.join(goals['exclude_elements']) or '未指定')]
    if any(goals[k] is not None for k in ('e_start_kV_cm', 'e_end_kV_cm')):
        lines.append(f"高级场路径：{val('e_start_kV_cm', 'kV/cm')} → {val('e_end_kV_cm', 'kV/cm')}")
    if goals['e_min_kV_cm'] is not None:
        lines.append('电场下限：' + val('e_min_kV_cm', 'kV/cm'))
    if goals['e_max_kV_cm'] is not None:
        lines.append('最大场强不超过：' + val('e_max_kV_cm', 'kV/cm'))
    if goals['include_elements']:
        lines.append('包含元素：' + '、'.join(goals['include_elements']))
    if goals['material_type']:
        lines.append('材料类型：' + goals['material_type'])
    if goals['delta_t_K'] is not None:
        lines.append(f"ΔT 目标：{goals['delta_t_operator']} {goals['delta_t_K']:g} K")
    if goals['maximize']:
        lines.append('研究偏好：尽可能大的 ΔT')
    return lines


def compact_conditions(goals):
    """Display only supplied conditions; preserve the full report formatter above."""
    lines=[]
    lo,hi=goals.get('temperature_min_K'),goals.get('temperature_max_K')
    if lo is not None and hi is not None:
        lines.append(f'温度：{lo:g} K' if lo==hi else f'温度：{lo:g}–{hi:g} K')
    elif lo is not None:
        lines.append(f'温度：≥ {lo:g} K')
    elif hi is not None:
        lines.append(f'温度：≤ {hi:g} K')
    start,end=goals.get('e_start_kV_cm'),goals.get('e_end_kV_cm')
    if start is not None and end is not None:
        lines.append(f'电场：{start:g} → {end:g} kV/cm')
    else:
        for label,value in [('电场起点',start),('电场终点',end)]:
            if value is not None:
                lines.append(f'{label}：{value:g} kV/cm')
    for label,key,unit in [('电场变化 ΔE','delta_E_kV_cm','kV/cm'),
                           ('电场下限','e_min_kV_cm','kV/cm'),('电场上限','e_max_kV_cm','kV/cm')]:
        if goals.get(key) is not None:
            lines.append(f'{label}：{goals[key]:g} {unit}')
    for label,key in [('排除元素','exclude_elements'),('包含元素','include_elements')]:
        if goals.get(key):
            lines.append(label+'：'+'、'.join(goals[key]))
    if goals.get('material_type'):
        lines.append('材料类型：'+goals['material_type'])
    if goals.get('delta_t_K') is not None:
        lines.append(f"ΔT 目标：{goals['delta_t_operator']} {goals['delta_t_K']:g} K")
    if goals.get('maximize'):
        lines.append('研究偏好：尽可能大的 ΔT')
    return lines
