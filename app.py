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
from ecalx_local.bst_ui import bst_page

from ecalx_local.setup_ui import setup_page, data_page
from ecalx_local.titles import research_title
st.set_page_config(page_title='ECalX · 材料研究工作台',page_icon='◈',layout='wide')
theme()
agent=LocalAgent(STORAGE)
# Keep draft widget values while their page is hidden (Streamlit cleans up
# unrendered widget keys otherwise). Inputs outside a form sync before navigation.
for key in list(st.session_state):
    if key.startswith(('home_', 'manual_', 'bst_')):
        st.session_state[key] = st.session_state[key]


def navigate_main():
    st.session_state.workspace = st.session_state.main_navigation


def open_settings():
    st.session_state.workspace = '设置'
    st.session_state.main_navigation = None



def select_settings(page):
    st.session_state.settings_page = page


def open_history(task_id=None):
    st.session_state.workspace='研究与历史'
    st.session_state.main_navigation='研究与历史'
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
    return {'研究与历史':'研究工作台','历史研究':'历史研究','离线模型演示':'离线模型探索','BST电卡插值':'BST电卡插值','研究报告':'研究报告','设置':'设置'}[value]


with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-mark">◈</span>ECalX</div>',unsafe_allow_html=True)
    st.caption(f'AI 材料研究工作台 · {__version__}')
    st.button('＋ 新建研究',use_container_width=True,on_click=open_history,type='primary')
    if 'workspace' not in st.session_state:
        st.session_state.workspace={'exploratory':'离线模型演示','bst':'BST电卡插值'}.get(st.query_params.get('view'),'研究与历史')
        st.session_state.main_navigation=st.session_state.workspace
    st.radio('研究工作区',(['研究与历史','历史研究','离线模型演示','BST电卡插值','研究报告'] if PERSONAL else ['研究与历史','历史研究','BST电卡插值','研究报告']),
             format_func=nav_label,key='main_navigation',label_visibility='collapsed',on_change=navigate_main)
    workspace=st.session_state.workspace
    tasks=agent.list_tasks()
    linked=st.query_params.get('task')
    if 'task_id' not in st.session_state and linked in {t['id'] for t in tasks}:
        st.session_state.task_id=linked
    st.divider()
    st.caption('最近研究')
    for task in tasks[:8]:
        title=research_title(task).replace('\n',' ')
        state=STATUS_LABELS.get(task['status'],task['status'])
        st.button(state+' · '+title,key='history_'+task['id'],use_container_width=True,
                  on_click=open_history,args=(task['id'],),help=title)
    if not tasks:
        st.caption('你的研究会自动保存在这里。')
    with st.container(key='settings_footer'):
        st.divider()
        st.caption('数据与研究保存在本机')
        st.button('设置',icon=':material/settings:',use_container_width=True,on_click=open_settings)


if workspace=='设置':
    settings_page=st.session_state.get('settings_page','API 配置')
    with st.container(key='settings_switch'):
        columns=st.columns(2,gap='small')
        for column,page in zip(columns,['API 配置','数据库构建']):
            column.button(page,key='settings_'+page,use_container_width=True,
                          type='primary' if settings_page==page else 'secondary',
                          on_click=select_settings,args=(page,))
    if settings_page=='API 配置':
        setup_page()
    else:
        data_page()
elif workspace=='离线模型演示':
    demo_page()
elif workspace=='BST电卡插值':
    bst_page()
elif workspace in {'历史研究','研究报告'}:
    report_view=workspace=='研究报告'
    section_header('研究工作区',workspace,'每次研究独立保存，随时回到证据与对话。' if not report_view else '下载真实研究结果与阶段报告。')
    query=st.text_input('搜索研究',placeholder='按研究问题搜索…')
    filtered=[t for t in tasks if query.casefold() in research_title(t).casefold()]
    if not filtered:
        st.info('暂无匹配研究。点击侧栏“新建研究”开始。')
    for task in filtered:
        with st.container(border=True):
            st.markdown('**'+research_title(task).replace('\n',' ')+'**')
            mode='DeepSeek 在线研究' if task['client_mode']=='LIVE_LLM' else '本地研究'
            st.caption(STATUS_LABELS.get(task['status'],task['status'])+' · '+mode)
            if report_view:
                downloads(task,agent)
            else:
                st.button('打开研究',key='open_'+task['id'],on_click=open_history,args=(task['id'],))
elif st.session_state.get('task_id'):
    research(agent,STORAGE,open_history)
else:
    home(STORAGE)
