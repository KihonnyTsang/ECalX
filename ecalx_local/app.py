"""ECalX Streamlit shell. Scientific runtime, tools and storage stay unchanged."""
from pathlib import Path
import os
import sys
from datetime import datetime
from html import escape
import streamlit as st
sys.dont_write_bytecode=True
from ecalx_local import APP_ROOT, PERSONAL, STORAGE, SCIENCE_ROOT
from ecalx_local.ui_data import ui_agent
from ecalx_local.ui import theme, section_header, STATUS_LABELS
from ecalx_local.ui_home import home
from ecalx_local.titles import research_title
st.set_page_config(page_title='ECalX · 材料研究工作台',page_icon='◈',layout='wide')
theme()
agent=ui_agent(str(STORAGE),str(SCIENCE_ROOT),PERSONAL)
# Keep draft widget values while their page is hidden (Streamlit cleans up
# unrendered widget keys otherwise). Inputs outside a form sync before navigation.
for key in list(st.session_state):
    if key.startswith(('home_', 'manual_', 'bst_', 'sim_', 'dt_')):
        st.session_state[key] = st.session_state[key]


def navigate_main(page):
    st.session_state.workspace = page
    st.session_state.main_navigation = page


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
        for key in ('task_id','prepared','review_signature','research_mode','literature_draft'):
            st.session_state.pop(key,None)
        for name in ('question','composition','tmin','tmax','edelta','exclude','allowed','estart','eend','emin','emax','peak'):
            st.session_state['home_'+name]=''
        from ecalx_local.field_protocol import CHOICES
        st.session_state.update(home_tunit='°C',home_funit='kV/cm',home_protocol=CHOICES[0],
                                home_mode='本地研究 · 无需联网',home_confirmed=False,home_paid=False)
    else:
        st.session_state.task_id=task_id
        st.query_params['task']=task_id


def nav_label(value):
    return {'研究与历史':'研究工作台','历史研究':'历史研究','离线模型演示':'离线模型探索','研究报告':'研究报告','设置':'设置'}[value]


with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-mark">◈</span>ECalX</div>',unsafe_allow_html=True)
    st.caption('AI 材料研究工作台')
    st.button('新建研究',icon=':material/add:',use_container_width=True,on_click=open_history)
    if 'workspace' not in st.session_state:
        st.session_state.workspace = '离线模型演示' if PERSONAL and st.query_params.get('view')=='exploratory' else '研究与历史'
        st.session_state.main_navigation=st.session_state.workspace
    if st.session_state.workspace == 'BST电卡插值':
        st.session_state.workspace = st.session_state.main_navigation = '研究与历史'
    with st.container(key='main_nav'):
        pages=[('研究与历史',':material/science:'),('历史研究',':material/history:'),('研究报告',':material/description:')]
        for page,icon in pages:
            st.button(nav_label(page),icon=icon,key='nav_'+page,use_container_width=True,
                      type='primary' if st.session_state.workspace==page else 'secondary',
                      on_click=navigate_main,args=(page,))
    workspace=st.session_state.workspace
    tasks,_=agent.task_page(limit=4)
    linked=st.query_params.get('task')
    if 'task_id' not in st.session_state and linked and agent.has_task(linked):
        st.session_state.task_id=linked
    with st.container(key='recent_research'):
        st.caption('最近研究')
        for task in tasks[:4]:
            title=research_title(task).replace('\n',' ')
            st.button(title,key='history_'+task['id'],use_container_width=True,
                      on_click=open_history,args=(task['id'],),help=title)
        if not tasks:
            st.caption('暂无研究记录')
    with st.container(key='settings_footer'):
        st.button('设置',icon=':material/settings:',use_container_width=True,
                  type='primary' if workspace=='设置' else 'secondary',on_click=open_settings)
        st.caption('保存在本机')


if workspace=='设置':
    from ecalx_local.setup_ui import setup_page, data_page
    section_header('', '设置')
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
    from ecalx_local.exploratory_ui import demo_page
    demo_page()
elif workspace in {'历史研究','研究报告'}:
    report_view=workspace=='研究报告'
    section_header('',workspace)
    query=st.text_input('搜索研究',placeholder='搜索研究主题',label_visibility='collapsed')
    filtered,total=agent.task_page(query=query)
    pages=max(1,(total+11)//12)
    if pages>1:
        page=st.number_input('历史页码',min_value=1,max_value=pages,value=1,step=1,key='history_page_'+workspace+'_'+query)
        filtered,_=agent.task_page(query=query,offset=(page-1)*12)
        st.caption(f'共 {total} 项研究 · 每页最多 12 项')
    if not filtered:
        st.caption('暂无研究记录' if not tasks else '没有找到匹配的研究')
    if report_view and filtered:
        chosen=st.selectbox('选择研究报告',[t['id'] for t in filtered],
                            format_func=lambda tid:research_title(next(t for t in filtered if t['id']==tid)))
        from ecalx_local.ui_research import report_summary
        report_summary(agent.get(chosen),agent)
    else:
        for task in filtered:
            with st.container(key='record_'+task['id'],border=True):
                a,b=st.columns([6,1],vertical_alignment='center')
                a.markdown('**'+escape(research_title(task).replace('\n',' '))+'**')
                stamp=task.get('created_at') or ''
                try:
                    from zoneinfo import ZoneInfo
                    stamp=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
                except (ValueError,TypeError):
                    stamp=''
                a.caption(' · '.join(filter(None,[stamp,STATUS_LABELS.get(task['status'],'待查看')])))
                b.button('打开研究',key='open_'+task['id'],on_click=open_history,args=(task['id'],),use_container_width=True)

elif st.session_state.get('task_id'):
    from ecalx_local.ui_research import research
    research(agent,STORAGE,open_history)
else:
    home(STORAGE)
