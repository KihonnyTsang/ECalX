"""Local protocol views, separate from imported facts and frozen numeric goals."""
import math
from copy import deepcopy

UNKNOWN = '文献未说明 / 协议未知'
PULSE = '常用脉冲：0 → 峰值电场 → 保持 → 0'
CUSTOM = '自定义电场起点、终点与变化幅度'
CHOICES = [UNKNOWN, PULSE, CUSTOM]


def reference_protocol(choice=UNKNOWN, *, peak=None, start=None, end=None, delta=None):
    if choice not in CHOICES:
        raise ValueError('请选择电场协议')
    for value in (peak, start, end, delta):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError('电场幅值必须为非负有限数值')
    if choice == PULSE and (peak is None or peak <= 0):
        raise ValueError('常用脉冲请单独填写正数峰值；不使用电场上限或 ΔE 代填')
    if choice == CUSTOM and start is not None and end is not None and delta is not None:
        if not math.isclose(abs(end-start), delta, abs_tol=1e-8):
            raise ValueError('自定义起终点与 ΔE 矛盾')
    return {'schema_version': 'field-reference-v1',
        'kind': {UNKNOWN: 'unknown', PULSE: 'standard_pulse', CUSTOM: 'custom'}[choice],
        'status': 'UNKNOWN' if choice == UNKNOWN else 'ASSUMED',
        'origin': 'user_reference', 'experimentally_verified': False,
        'peak_kV_cm': peak if choice == PULSE else None,
        'start_kV_cm': start if choice == CUSTOM else None,
        'end_kV_cm': end if choice == CUSTOM else None,
        'delta_kV_cm': delta if choice == CUSTOM else None,
        'reference_path_kV_cm': [0.0, peak, peak, 0.0] if choice == PULSE else None,
        'hold_seconds': None, 'rise_rate_kV_cm_s': None, 'fall_rate_kV_cm_s': None,
        'measurement_branch': None}


def has_explicit_path(protocol):
    """Check geometry/kind, never let an UNKNOWN label erase a specified path."""
    p = protocol or {}
    return (p.get('kind') == 'standard_pulse' or bool(p.get('reference_path_kV_cm'))
            or p.get('start_kV_cm') is not None or p.get('end_kV_cm') is not None)


def user_reference(protocol):
    """An analysis input can never self-certify as an OBSERVED experiment."""
    p = deepcopy(protocol or reference_protocol())
    p.update(origin='user_reference', experimentally_verified=False,
             status='ASSUMED' if has_explicit_path(p) or p.get('kind') == 'custom' else 'UNKNOWN')
    return p


def protocol_lines(protocol=None):
    p = protocol or reference_protocol()
    if p['status'] == 'OBSERVED':
        return ['已核实实验协议 · OBSERVED：仅来自独立核验的协议证据区；按原条件匹配。明确路径仍受模型适用域限制。']
    if p['status'] == 'UNKNOWN':
        return ['协议未知 · UNKNOWN：端点缺失不能确认路径匹配；仅有 ΔE 不代表 0→ΔE。预测仍须通过原适用域检查。']
    lines = ['用户假设协议 · ASSUMED：用于参考与探索，不能证明论文采用了该协议，也不能补齐原始数据。']
    if p['kind'] == 'standard_pulse':
        lines += [f"参考路径：0 → {p['peak_kV_cm']:g} → 保持 → 0 kV/cm。",
                  '证据匹配只核对参考升场段的端点；不能确认完整脉冲、保持或测量分支相同。']
    else:
        lines += ['自定义条件仅按已填写端点 / ΔE 核对；缺失项保持未知。']
    lines += ['保持时间、升降速率、测量分支：未知；不自动生成。',
              '预测影响：明确路径（包括假设脉冲）不受当前 RF/XGBoost 支持，将拒答；不得改标“路径未知”。']
    return lines


def imported_protocol(record):
    """Uploaded numbers or protocol text are user supplied, never verified papers."""
    r = record.to_dict() if hasattr(record, 'to_dict') else record
    raw = r.get('raw_metadata_json') or {}
    return {'status': 'UNKNOWN', 'origin': 'user_supplied_unverified',
            'experimentally_verified': False, 'kind': 'unknown',
            'start_kV_cm': r.get('e_start_kV_cm'), 'end_kV_cm': r.get('e_end_kV_cm'),
            'peak_kV_cm': raw.get('actual_peak_kV_cm'),
            'delta_kV_cm': r.get('e_delta_kV_cm'), 'upper_bound_kV_cm': r.get('e_max_kV_cm'),
            'method': r.get('measurement_method'), 'protocol_text': raw.get('protocol')}
