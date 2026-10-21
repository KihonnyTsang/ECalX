"""Read-only titles shared by research, history and downloaded reports."""


def research_title(task):
    original = (task.get('original') or '').strip()
    if original:
        return original
    goals = task.get('frozen_goals') or {}
    parts = []
    def n(value):
        return f'{round(value, 8):g}'
    low, high = goals.get('temperature_min_K'), goals.get('temperature_max_K')
    if low is not None and high is not None:
        parts.append(n(low - 273.15) + '–' + n(high - 273.15) + '°C')
    elif low is not None:
        parts.append('≥' + n(low - 273.15) + '°C')
    elif high is not None:
        parts.append('≤' + n(high - 273.15) + '°C')
    if goals.get('delta_E_kV_cm') is not None:
        parts.append('ΔE ' + n(goals['delta_E_kV_cm']) + ' kV/cm')
    start, end = goals.get('e_start_kV_cm'), goals.get('e_end_kV_cm')
    if start is not None or end is not None:
        parts.append('场路径 ' + (n(start) if start is not None else '未知') + '→' +
                     (n(end) if end is not None else '未知') + ' kV/cm')
    for key, label in [('e_min_kV_cm', '电场下限 '), ('e_max_kV_cm', '电场上限 ')]:
        if goals.get(key) is not None:
            parts.append(label + n(goals[key]) + ' kV/cm')
    for key, label in [('include_elements', '包含 '), ('exclude_elements', '排除 ')]:
        if goals.get(key):
            parts.append(label + '、'.join(goals[key]))
    if goals.get('material_type'):
        parts.append(goals['material_type'])
    if goals.get('delta_t_K') is not None:
        parts.append('ΔT ' + (goals.get('delta_t_operator') or '') + n(goals['delta_t_K']) + ' K')
    if goals.get('maximize'):
        parts.append('尽可能大的 ΔT')
    return '有条件材料探索 · ' + ' · '.join(parts) if parts else '开放探索 · 未指定研究条件'
