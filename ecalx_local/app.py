"""ECalX Streamlit shell. Scientific runtime, tools and storage stay unchanged."""
from pathlib import Path
import os
import sys
import streamlit as st
sys.dont_write_bytecode=True
from ecalx_local import APP_ROOT, PERSONAL, STORAGE
from ecalx import __version__
from ecalx_local.engine import LocalAgent
from ecalx_local.ui import theme, section_header, STATUS_LABELS
from ecalx_local.ui_home import home
from ecalx_local.ui_research import research, downloads
from ecalx_local.exploratory_ui import demo_page

from ecalx_local.setup_ui import setup_page, data_page
st.set_page_config(page_title='ECalX · 材料研究工作台',page_icon='◈',layout='wide')
theme()
agent=LocalAgent(STORAGE)


def open_history(task_id=None):
    st.session_state.workspace='研究与历史'
    st.query_params.pop('view',None)
    st.query_params.pop('task',None)
    st.session_state.pop('research_change_draft',None)
    if task_id is None:
        for key in list(st.session_state):
            if key.startswith('home_') or key in {'task_id','prepared','review_signature','research_mode'}:
                st.session_state.pop(key,None)
    else:
        st.session_state.task_id=task_id
        st.query_params['task']=task_id


def nav_label(value):
    return {'研究与历史':'研究工作台','历史研究':'历史研究','离线模型演示':'离线模型探索','研究报告':'研究报告','首次配置':'首次配置','导入数据':'我的材料数据'}[value]


with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-mark">◈</span>ECalX</div>',unsafe_allow_html=True)
    st.caption(f'AI 材料研究工作台 · {__version__}')
    st.button('＋ 新建研究',use_container_width=True,on_click=open_history,type='primary')
    if 'workspace' not in st.session_state:
        st.session_state.workspace='离线模型演示' if st.query_params.get('view')=='exploratory' else '研究与历史'
    workspace=st.radio('研究工作区',(['研究与历史','历史研究','离线模型演示','研究报告'] if PERSONAL else ['研究与历史','历史研究','研究报告','首次配置','导入数据']),
                       format_func=nav_label,key='workspace',label_visibility='collapsed')
    tasks=agent.list_tasks()
    linked=st.query_params.get('task')
    if 'task_id' not in st.session_state and linked in {t['id'] for t in tasks}:
        st.session_state.task_id=linked
    st.divider()
    st.caption('最近研究')
    for task in tasks[:8]:
        title=(task['original'] or '开放探索').replace('\n',' ')
        state=STATUS_LABELS.get(task['status'],task['status'])
        st.button(state+' · '+title[:24],key='history_'+task['id'],use_container_width=True,
                  on_click=open_history,args=(task['id'],),help=title)
    if not tasks:
        st.caption('你的研究会自动保存在这里。')
    st.divider()
    st.caption('本机科研数据与工具\n\nM5 正式推荐未启用')

if workspace=='首次配置':
    setup_page()
elif workspace=='导入数据':
    data_page()
elif workspace=='离线模型演示':
    demo_page()
elif workspace in {'历史研究','研究报告'}:
    report_view=workspace=='研究报告'
    section_header('研究工作区',workspace,'每次研究独立保存，随时回到证据与对话。' if not report_view else '下载真实研究结果与阶段报告。')
    query=st.text_input('搜索研究',placeholder='按研究问题搜索…')
    filtered=[t for t in tasks if query.casefold() in (t['original'] or '开放探索').casefold()]
    if not filtered:
        st.info('暂无匹配研究。点击侧栏“新建研究”开始。')
    for task in filtered:
        with st.container(border=True):
            st.markdown('**'+(task['original'] or '开放探索').replace('\n',' ')+'**')
            mode='DeepSeek Agent' if task['client_mode']=='LIVE_LLM' else '本地科研工具'
            st.caption(STATUS_LABELS.get(task['status'],task['status'])+' · '+mode)
            if report_view:
                downloads(task,agent)
            else:
                st.button('打开研究',key='open_'+task['id'],on_click=open_history,args=(task['id'],))
elif st.session_state.get('task_id'):
    research(agent,STORAGE,open_history)
else:
    if not PERSONAL:
        st.info('BYO API & Data · 无 Key 可离线研究；请先从侧栏“我的材料数据”导入你自己的 CSV/Excel。“首次配置”可设置在线 Key 与预算。')
    home(STORAGE)
