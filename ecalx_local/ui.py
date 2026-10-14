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
    mode = 'DeepSeek Agent' if task['client_mode']=='LIVE_LLM' else '本地科研工具'
    st.markdown('<div class="task-meta">'+badge(STATUS_LABELS.get(task['status'],task['status']),tone)
        +f'<span>{mode}</span><span>自动保存到本机</span></div>', unsafe_allow_html=True)


def science_note():
    st.markdown('<div class="science-note"><strong>研究边界</strong> · 新组成均为 generated_unverified；'
        '正式 ΔT / ΔS 为 unknown，M5 禁用。化学校验与文献种子不能证明候选性能，探索估计不用于排名。</div>', unsafe_allow_html=True)
