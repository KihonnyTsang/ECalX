"""Research entry and confirmation: UI only, original goals and budget gates."""
from contextlib import nullcontext
import streamlit as st
from . import PERSONAL
from .inputs import prepare_inputs, goals_summary
from .titles import research_title
from .engine import online_quote
from .preflight import readonly_preflight
from .conditions import SEMANTICS
from .jobs import create_research, launch
from .ui import section_header, protocol_label, protocol_summary, input_error
from .field_protocol import CHOICES


def show_quote(q):
    st.write(f"{q['expected_requests']}；最多 **{q['max_requests']} 次请求 / {q['max_tokens']:,} Token**。")
    if PERSONAL:
        st.write(f"保守估算不超过 **¥{q['max_estimated_cny']:.2f}**。")
        st.caption(q['notice']+' 单价有效期：'+q['rate_valid_through'])
    else:
        st.write(f"每日最多 {q['daily_requests']} 次请求 / {q['daily_tokens']:,} Token（UTC）；每任务最多 {q['max_tool_calls']} 次工具调用，每请求最多输出 {q['output_tokens']:,} Token。")
        st.caption(q['notice'])
    st.write(f"单次 {q['request_seconds']:g} 秒，任务 {q['task_seconds']:g} 秒，自动重试 0 次。")
    st.caption('发送需求、已确认条件、工具定义和最小文献事实；经授权的请求记入原共享 API 账本。' if PERSONAL else q['scope']+' 请求仅记入你自己的本地 API 账本。')


def use_example(text):
    st.session_state['home_question'] = text
    st.session_state.pop('prepared', None)


def home(storage):
    section_header('', '材料研究')
    if st.session_state.get('prepared'):
        st.subheader('确认研究条件')
        review_conditions(storage)
        return
    # Validate cached results against the same current inputs before giving
    # results priority. Editing any field invalidates the previous result.
    keys={'natural_language':'question','composition':'composition','temperature_min':'tmin',
          'temperature_max':'tmax','temperature_unit':'tunit','field_unit':'funit',
          'e_delta':'edelta','e_start':'estart','e_end':'eend','e_min':'emin','e_max':'emax',
          'exclude':'exclude','allowed_elements':'allowed','field_protocol':'protocol','e_peak':'peak'}
    raw_before={field:st.session_state.get('home_'+key, '') for field,key in keys.items()}
    raw_before.update(temperature_unit=raw_before['temperature_unit'] or '°C',
                      field_unit=raw_before['field_unit'] or 'kV/cm',
                      field_protocol=raw_before['field_protocol'] or CHOICES[0],
                      e_delta_unit='kV/cm',field_kind='起点 → 终点')
    cached=st.session_state.get('literature_draft')
    show_result=False
    if cached:
        try:
            from .bst_research import signature
            show_result=cached['input_sha256']==signature(prepare_inputs(raw_before))
        except ValueError:
            pass
    if show_result:
        from .bst_ui import render_result
        render_result(cached)
    editor=st.expander('编辑研究问题与条件',expanded=False) if show_result else nullcontext()
    with editor:
        with st.container(key='research_inputs'):
            natural=st.text_area('研究问题',key='home_question',label_visibility='collapsed',
                placeholder='输入研究问题，或在下方填写材料与研究条件…',height=108)
            st.subheader('研究条件')
            a,b=st.columns([2,1])
            composition=a.text_input('材料组成',placeholder='如 Ba0.8Sr0.2TiO3',key='home_composition')
            exclude=b.text_input('排除元素',placeholder='选填，如 Pb',key='home_exclude')
            allowed=st.text_input('允许使用的元素',placeholder='选填，如 Ba、Sr、Ca、Ti、O；每个候选可只使用其中一部分',key='home_allowed')
            if allowed.strip():
                st.caption('组成搜索：同价位点替代，5%–25% 工程网格；不代表可合成范围。')
            a,b,c,d=st.columns([1,1,.75,1.45])
            tmin=a.text_input('温度下限',placeholder='不限',key='home_tmin')
            tmax=b.text_input('温度上限',placeholder='不限',key='home_tmax')
            tunit=c.selectbox('温度单位',['°C','K'],key='home_tunit')
            edelta=d.text_input('电场变化 ΔE（kV/cm）',placeholder='选填',key='home_edelta')
            with st.expander('高级条件',expanded=False):
                a,b=st.columns(2)
                funit=a.selectbox('电场单位',['kV/cm','kV/mm','MV/m'],key='home_funit')
                protocol=b.selectbox('加电方式',CHOICES,format_func=protocol_label,key='home_protocol')
                estart=a.text_input('电场起点',placeholder='未指定',key='home_estart')
                eend=b.text_input('电场终点',placeholder='未指定',key='home_eend')
                emin=a.text_input('电场下限',placeholder='未指定',key='home_emin')
                emax=b.text_input('电场上限',placeholder='未指定',key='home_emax')
                peak=st.text_input('实际峰值电场（常用脉冲）',placeholder='按实际条件填写',key='home_peak')
                st.caption('起点、终点、峰值与上限分别填写；不使用 ΔE 代填。')
                selected_mode=st.radio('研究方式',['本地研究 · 无需联网','DeepSeek · 在线研究'],
                                       horizontal=True,key='home_mode')
                st.caption('在线研究开始前需确认用量与发送内容。')
            a,b=st.columns([1,2],vertical_alignment='center')
            submitted=a.button('开始研究',type='primary',use_container_width=True)
            b.caption('条件选填，留空表示未指定。')
    raw={'natural_language':natural,'composition':composition,'temperature_min':tmin,'temperature_max':tmax,
         'temperature_unit':tunit,'e_delta':edelta,'e_delta_unit':'kV/cm','field_kind':'起点 → 终点',
         'field_unit':funit,'e_start':estart,'e_end':eend,'e_min':emin,'e_max':emax,'exclude':exclude,'allowed_elements':allowed,
         'field_protocol':protocol,'e_peak':peak}
    signature=[raw,selected_mode]
    prepared=None
    try:
        prepared=prepare_inputs(raw)
    except ValueError:
        pass
    if prepared is not None:
        from .bst_ui import research_tool
        research_tool(prepared,show_cached=not show_result)
    if submitted:
        st.session_state['home_confirmed']=False
        st.session_state['home_paid']=False
        try:
            st.session_state.prepared=prepared if prepared is not None else prepare_inputs(raw)
            st.session_state.review_signature=signature
            st.session_state.research_mode='live' if selected_mode.startswith('DeepSeek') else 'local'
            st.rerun()
        except ValueError as error:
            st.session_state.pop('prepared',None)
            st.error(input_error(error))


def edit_conditions():
    raw,mode=st.session_state.review_signature
    keys={'natural_language':'question','composition':'composition','temperature_min':'tmin','temperature_max':'tmax',
          'temperature_unit':'tunit','field_unit':'funit',
          'e_start':'estart','e_end':'eend','e_min':'emin','e_max':'emax','exclude':'exclude','allowed_elements':'allowed','e_delta':'edelta',
          'field_protocol':'protocol','e_peak':'peak'}
    for field,key in keys.items():
        if field in raw:
            st.session_state['home_'+key]=raw[field]
    st.session_state.home_mode=mode
    st.session_state.pop('prepared',None)
    st.session_state.home_confirmed=False
    st.session_state.home_paid=False


def review_conditions(storage):
    p = st.session_state.prepared
    with st.container():
        st.write(research_title({'original':p['raw'].get('natural_language'), 'frozen_goals':p['goals']}))
    st.caption('本地研究 · 无需联网' if st.session_state.research_mode=='local' else 'DeepSeek · 需确认用量')
    with st.container(border=True):
        st.subheader('研究条件')
        if p['raw'].get('composition'):
            st.write('研究组成：'+p['raw']['composition'])
        if p.get('allowed_elements'):
            st.write('允许元素：'+'、'.join(p['allowed_elements']))
        from .input_display import compact_conditions
        for line in compact_conditions(p['goals']):
            st.write(line)
        if p['field_protocol'].get('kind') != 'unknown':
            with st.expander('高级详情 · 加电条件说明'):
                from .field_protocol import protocol_lines
                for line in protocol_lines(p['field_protocol']):
                    st.write(line)
        if p['pending']:
            with st.expander('研究偏好'):
                for item in p['pending']:
                    st.write(item.removeprefix('未解析文本：'))
        from .bst_ui import research_tool
        research_tool(p)
        confirmed = st.checkbox('确认以上研究条件',key='home_confirmed')
        consent,ready = None,True
        if st.session_state.research_mode=='live':
            with st.expander('在线研究准备检查', expanded=True):
                preview = readonly_preflight(p['goals'])
                for line in preview['checks']:
                    st.write(line)
                st.caption(preview['notice'])
            try:
                q = online_quote()
                show_quote(q)
                paid = st.checkbox('即将调用在线模型。我同意本次数据发送范围和上述请求预算。',key='home_paid')
                consent,ready = (q if paid else None),paid
            except Exception:
                ready=False
                st.warning('在线配置或本地预算当前不可用。未发起请求；可返回选择本地方式。')
        if st.button('确认并开始研究',type='primary',disabled=not(confirmed and ready),use_container_width=True):
            try:
                task=create_research(p,st.session_state.research_mode,confirmed=True,consent=consent,storage=storage)
                st.session_state.task_id=task['id']
                st.query_params['task']=task['id']
                launch(task['id'],storage)
                st.rerun()
            except ValueError as error:
                st.error(input_error(error))
    st.button('返回修改研究条件',on_click=edit_conditions)
