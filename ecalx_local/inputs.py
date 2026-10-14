"""Host parsing and explicit unit conversion, before any provider is constructed."""
import re
from ecalx.agent.contracts import Goals
from ecalx.parsing.requirements import parse_request
from ecalx.parsing.units import finite_number
from ecalx.materials.formulas import validate_symbols
from .input_display import field_language, condition_lines
from ecalx.parsing.units import field_kV_cm
from .field_protocol import UNKNOWN, PULSE, CUSTOM, reference_protocol, protocol_lines


def prepare_inputs(raw):
    raw = dict(raw)
    raw.setdefault('field_unit', 'kV/cm')
    if raw.get('temperature_unit', '°C') not in {'°C', 'K'}:
        raise ValueError('温度单位必须为 °C 或 K')
    if raw.get('field_unit', 'kV/cm') not in {'kV/cm', 'kV/mm', 'MV/m'}:
        raise ValueError('电场单位必须为 kV/cm、kV/mm 或 MV/m')
    # Structured boxes accept numbers only; the adjacent unit is authoritative.
    for name in ('temperature_min', 'temperature_max', 'e_start', 'e_end', 'e_min', 'e_max', 'field_value', 'e_peak', 'e_delta'):
        value = raw.get(name)
        if value is not None and str(value).strip():
            finite_number(value, name)
    symbols = [s for s in re.split(r'[,，、;；\s]+', raw.get('exclude', '').strip()) if s]
    validate_symbols(symbols)
    raw['elements'] = '排除 ' + ','.join(symbols) if symbols else ''
    kind = raw.pop('field_kind', '上限')
    value = raw.pop('field_value', '')
    choice = raw.get('field_protocol', UNKNOWN)
    peak = field_kV_cm(raw.get('e_peak'), raw.get('field_unit', 'kV/cm'))
    if choice == PULSE:
        if str(raw.get('e_start') or '').strip() or str(raw.get('e_end') or '').strip():
            raise ValueError('常用脉冲使用单独的峰值；自定义端点请选择自定义协议')
        protocol = reference_protocol(choice, peak=peak)
        # Compatibility projection: only a reference loading segment, never
        # imported endpoints or a claim that the full pulse was observed.
        raw.update(e_start=0.0, e_end=raw['e_peak'])
    elif peak is not None:
        raise ValueError('填写峰值时请选择常用脉冲；上限与 ΔE 请使用各自输入框')
    if kind == '上限':
        if 'e_max' not in raw or str(value).strip():
            raw['e_max'] = value
    elif kind == '变化幅度 ΔE':
        raw['electric_field'] = f"ΔE={value} {raw['field_unit']}" if str(value).strip() else ''
    elif kind != '起点 → 终点':
        raise ValueError('请选择电场条件的含义')
    if str(raw.get('e_delta') or '').strip():
        delta_unit = raw.get('e_delta_unit', raw['field_unit'])
        delta = field_kV_cm(raw['e_delta'], delta_unit)
        if delta <= 0:
            raise ValueError('电场变化幅度 ΔE 必须为正数；不限制时请留空')
        if kind == '变化幅度 ΔE' and str(value).strip() and field_kV_cm(value, raw['field_unit']) != delta:
            raise ValueError('两处 ΔE 输入不一致，请确认后修改')
        raw['electric_field'] = f"ΔE={delta} kV/cm"
    parsed = parse_request({**raw, 'natural_language': field_language(raw.get('natural_language', ''))})
    goals = Goals(**{k: v for k, v in parsed.request.to_dict().items() if k in Goals.model_fields}).model_dump()
    if choice != PULSE:
        endpoints = any(goals[k] is not None for k in ('e_start_kV_cm', 'e_end_kV_cm'))
        if endpoints and 'field_protocol' in raw and choice == UNKNOWN and (str(raw.get('e_start') or '').strip() or str(raw.get('e_end') or '').strip()):
            raise ValueError('已填写明确端点，请选择自定义电场协议；不能标为协议未知')
        protocol = reference_protocol(CUSTOM if endpoints else choice,
            start=goals['e_start_kV_cm'], end=goals['e_end_kV_cm'], delta=goals['delta_E_kV_cm'])
    return {'raw': raw, 'goals': goals, 'field_protocol': protocol, 'pending': list(parsed.pending),
            'summary': goals_summary(goals) + (protocol_lines(protocol) if choice == PULSE else [])}


def goals_summary(goals):
    return condition_lines(goals)
