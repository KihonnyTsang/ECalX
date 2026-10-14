"""Opt-in references; no writes to evidence records."""
import streamlit as st
from .field_protocol import CHOICES, UNKNOWN, PULSE, CUSTOM, reference_protocol, protocol_lines
from ecalx.parsing.units import field_kV_cm


def reference_controls(prefix):
    choice = st.selectbox('电场协议', CHOICES, key=prefix+'_protocol')
    enabled = True
    peak = start = end = delta = None
    if choice == PULSE:
        enabled = st.checkbox('假设采用标准脉冲协议（仅探索，ASSUMED）', key=prefix+'_assume')
        peak = st.text_input('参考峰值电场 / kV/cm', placeholder='手动填写；上限与 ΔE 不会代填', key=prefix+'_peak')
    elif choice == CUSTOM:
        a,b,c = st.columns(3)
        start = a.text_input('参考起点 / kV/cm', key=prefix+'_start')
        end = b.text_input('参考终点 / kV/cm', key=prefix+'_end')
        delta = c.text_input('参考 ΔE / kV/cm', key=prefix+'_delta')
    if choice != UNKNOWN and enabled:
        st.caption('用户假设协议 · ASSUMED；不更新真实端点、测量方法或文献验证状态。')
    st.caption('已核实实验协议 · OBSERVED 只在独立核验的证据区显示。上传协议文字不等于核实；未知项保持 UNKNOWN。')
    return {'choice': choice if enabled else UNKNOWN, 'peak': peak if enabled else None,
            'start': start, 'end': end, 'delta': delta}


def build_reference(values):
    return reference_protocol(values['choice'], **{k:field_kV_cm(values[k] or '', 'kV/cm')
        for k in ('peak','start','end','delta')})


def show_protocol(protocol):
    for line in protocol_lines(protocol):
        st.write(line)
