"""Shared presentation primitives; no task or scientific state is changed here."""
from html import escape
from pathlib import Path
import re
import streamlit as st

STATUS_LABELS = {'DRAFT':'准备中', 'RUNNING':'研究中', 'AWAITING_APPROVAL':'待确认',
    'NEEDS_CLARIFICATION':'待补充', 'PAUSED':'已暂停', 'COMPLETED':'已完成',
    'CANCELLED':'已停止', 'FAILED':'执行未完成'}


def theme():
    st.markdown('<style>'+Path(__file__).with_name('ui.css').read_text()+'</style>', unsafe_allow_html=True)


def formula_html(formula):
    # Escape first: task text must never become arbitrary HTML.
    return re.sub(r'(\d+(?:\.\d+)?)', r'<sub>\1</sub>', escape(formula))


def formula_title(formula):
    st.markdown('<div class="formula">'+formula_html(formula)+'</div>', unsafe_allow_html=True)


def badge(text, tone='neutral'):
    tone = tone if tone in {'neutral','teal','risk'} else 'neutral'
    return f'<span class="badge {tone}">{escape(text)}</span>'


def section_header(kicker, title, description=''):
    st.markdown(f'<div class="eyebrow">{escape(kicker)}</div><h1>{escape(title)}</h1>'
                + (f'<p class="intro">{escape(description)}</p>' if description else ''), unsafe_allow_html=True)


def task_status(task):
    tone = 'risk' if task['status']=='FAILED' else 'teal' if task['status']=='COMPLETED' else 'neutral'
    mode = 'DeepSeek 在线研究' if task['client_mode']=='LIVE_LLM' else '本地研究'
    st.markdown('<div class="task-meta">'+badge(STATUS_LABELS.get(task['status'],task['status']),tone)
        +f'<span>{mode}</span><span>自动保存到本机</span></div>', unsafe_allow_html=True)


def science_note():
    st.markdown('<div class="science-note"><strong>说明</strong>：新材料仅作为待验证候选，'
        '当前不提供正式性能预测或排名。</div>', unsafe_allow_html=True)


def protocol_label(choice):
    from .field_protocol import UNKNOWN
    return '未指定加电方式' if choice == UNKNOWN else choice


def protocol_summary(protocol=None):
    """Short display copy; stored protocol states and report wording stay intact."""
    p = protocol or {}
    if p.get('status', 'UNKNOWN') == 'UNKNOWN':
        return '有些文献只给出了 ΔE，没有完整的加电路径。这类数据可用于检索和统计，但不能视为完全一致的实验条件。'
    if p.get('status') == 'OBSERVED':
        return '部分文献的加电方式已核实，具体记录见下方详情。'
    if p.get('kind') == 'standard_pulse':
        return f"本次参考加电方式：0 → {p['peak_kV_cm']:g} → 保持 → 0 kV/cm，仍需核对文献中的实际条件。"
    return '本次按你填写的电场起点、终点和 ΔE 核对；未填写的条件不作限制。'


def evidence_message(summary):
    if summary and summary.get('verified_path_matches', 0):
        return f"常规检索中没有完全一致的加电条件；另有 {summary['verified_path_matches']} 条已核实的文献记录符合条件。候选性能仍需验证。"
    if summary and summary.get('amplitude_only_auxiliary_matches', 0):
        return f"有 {summary['amplitude_only_auxiliary_matches']} 条记录符合 ΔE 幅度等条件，但加电路径尚未确认。候选性能仍需验证。"
    return '当前没有符合全部研究条件的实验记录，候选材料仍需后续实验验证。'
