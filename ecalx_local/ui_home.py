"""Research entry and confirmation: UI only, original goals and budget gates."""
import streamlit as st
from . import PERSONAL
from .inputs import prepare_inputs
from .engine import online_quote
from .preflight import readonly_preflight
from .conditions import SEMANTICS
from .jobs import create_research, launch
from .ui import section_header
from .field_protocol import CHOICES


def show_quote(q):
    st.write(f"{q['expected_requests']}；最多 **{q['max_requests']} 次请求 / {q['max_tokens']:,} Token**。")
    st.write(f"保守估算不超过 **¥{q['max_estimated_cny']:.2f}**；单次 {q['request_seconds']:g} 秒，任务 {q['task_seconds']:g} 秒，自动重试 0 次。")
    st.caption(q['notice']+' 单价有效期：'+q['rate_valid_through'])
    st.caption('发送需求、已确认条件、工具定义和最小文献事实；经授权的请求记入原共享 API 账本。' if PERSONAL else q['scope']+' 请求仅记入你自己的本地 API 账本。')


def use_example(text):
    st.session_state['home_question'] = text
    st.session_state.pop('prepared', None)


def home(storage):
    if st.session_state.get('prepared'):
        section_header('确认研究条件', '核对后，开始这次研究', '确认后冻结 Goals；后续修改需创建独立新研究。')
        review_conditions(storage)
        return
    st.markdown('<div class="home-top"></div>', unsafe_allow_html=True)
    section_header('ECALX · 材料研究工作台', '今天想研究什么材料？',
                   '从一个问题出发，沿着文献证据探索新的电卡材料。')
    with st.container(border=True):
        with st.form('research_input'):
            natural = st.text_area('研究问题', key='home_question', label_visibility='collapsed',
                placeholder='描述你的研究想法，例如：探索有潜力的无铅电卡材料，并说明候选依据…', height=160)
            st.caption('研究条件全部选填，留空表示未指定。')
            a,b,c = st.columns([1,1,.8])
            tmin = a.text_input('最低温度', placeholder='如 -20.5', key='home_tmin')
            tmax = b.text_input('最高温度', placeholder='如 80', key='home_tmax')
            tunit = c.selectbox('温度单位', ['°C','K'], key='home_tunit')
            edelta = st.text_input('电场变化幅度 ΔE（kV/cm）', placeholder='可留空；输入任意正数，如 20 或 12.5', key='home_edelta')
            st.caption('ΔE 只表示变化幅度，不推定起点、终点、峰值或升降场分支。')
            exclude = st.text_input('排除元素', placeholder='例如 Pb；多个元素用逗号或空格分隔', key='home_exclude')
            with st.expander('高级电场条件', expanded=False):
                st.caption('仅在需要明确场路径、场强范围或具体协议时填写。与研究问题冲突时，请确认并修改后重新核对。')
                funit = st.selectbox('高级电场单位', ['kV/cm','kV/mm','MV/m'], key='home_funit')
                a,b = st.columns(2)
                estart = a.text_input('电场起点', placeholder='可留空，不补 0', key='home_estart')
                eend = b.text_input('电场终点', placeholder='可留空', key='home_eend')
                emin = a.text_input('电场下限', placeholder='可留空', key='home_emin')
                emax = b.text_input('最大场强上限', placeholder='可留空；与 ΔE 分别核对', key='home_emax')
                protocol = st.selectbox('电场协议', CHOICES, key='home_protocol')
                peak = st.text_input('实际拟施加峰值电场（常用脉冲）', placeholder='单独填写，不使用上限或 ΔE 代填', key='home_peak')
                st.caption('常用脉冲为用户选择的参考协议，标记 ASSUMED；保持时间、升降速率和测量分支不自动补齐。')
                st.caption(SEMANTICS)
            with st.expander('研究方式 · 默认本地科研工具'):
                selected_mode = st.radio('研究方式', ['本地科研工具 · 无需联网','DeepSeek Agent · 在线研究'],
                    horizontal=True, key='home_mode')
                st.caption('本地方式运行真实科研工具，使用确定性流程。DeepSeek 在线规划和调用工具，在下一步单独确认费用与发送范围。')
            submitted = st.form_submit_button('检查研究条件', type='primary', use_container_width=True)
        raw = {'natural_language':natural,'temperature_min':tmin,'temperature_max':tmax,'temperature_unit':tunit,
               'e_delta':edelta,'e_delta_unit':'kV/cm','field_kind':'起点 → 终点','field_unit':funit,
               'e_start':estart,'e_end':eend,'e_min':emin,'e_max':emax,'exclude':exclude,
               'field_protocol':protocol,'e_peak':peak}
        signature = [raw,selected_mode]
        if submitted:
            # Every new review invalidates old confirmation, including identical inputs.
            st.session_state['home_confirmed'] = False
            st.session_state['home_paid'] = False
            try:
                st.session_state.prepared = prepare_inputs(raw)
                st.session_state.review_signature = signature
                st.session_state.research_mode = 'live' if selected_mode.startswith('DeepSeek') else 'local'
                st.rerun()
            except ValueError as error:
                st.session_state.pop('prepared',None)
                st.error('条件冲突：请确认自然语言与高级条件的真实要求，修改后重新核对。' + str(error) if '冲突' in str(error) or '矛盾' in str(error) else str(error))
    st.caption('试试一个研究方向，或直接写下你的想法')
    a,b = st.columns(2)
    a.button('探索无铅电卡材料',use_container_width=True,on_click=use_example,
             args=('探索有研究潜力的无铅电卡材料，并说明候选组成的依据。',))
    b.button('梳理材料的文献依据',use_container_width=True,on_click=use_example,
             args=('梳理电卡材料的文献依据，说明实验条件和现有研究缺口。',))
    st.markdown('<div class="home-foot"><span>真实本地文献与科研工具</span><span>研究条件由你确认</span><span>新组成为待验证候选</span></div>',unsafe_allow_html=True)


def edit_conditions():
    raw,mode=st.session_state.review_signature
    keys={'natural_language':'question','temperature_min':'tmin','temperature_max':'tmax',
          'temperature_unit':'tunit','field_unit':'funit',
          'e_start':'estart','e_end':'eend','e_min':'emin','e_max':'emax','exclude':'exclude','e_delta':'edelta',
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
    with st.chat_message('user'):
        st.write(p['raw'].get('natural_language') or '开放探索 · 未填写研究问题')
    st.caption('本地科研工具 · 无需联网' if st.session_state.research_mode=='local' else 'DeepSeek Agent · 需单独确认预算')
    with st.container(border=True):
        st.subheader('本次生效的条件')
        for line in p['summary']:
            st.write(line)
        if p['pending']:
            st.warning('以下内容尚未转换为硬条件，请核对；需要精确限制时返回修改。')
            for item in p['pending']:
                st.write('• '+item)
        confirmed = st.checkbox('我确认上述条件；未解析文本保留为研究偏好，不作硬筛选',key='home_confirmed')
        consent,ready = None,True
        if st.session_state.research_mode=='live':
            with st.expander('在线研究只读预检 · 未发送请求', expanded=True):
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
        if st.button('开始研究',type='primary',disabled=not(confirmed and ready),use_container_width=True):
            try:
                task=create_research(p,st.session_state.research_mode,confirmed=True,consent=consent,storage=storage)
                st.session_state.task_id=task['id']
                st.query_params['task']=task['id']
                launch(task['id'],storage)
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    st.button('返回修改研究条件',on_click=edit_conditions)
