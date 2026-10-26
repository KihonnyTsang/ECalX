"""Human-readable follow-up UI and explicit child-research editor."""
import json
import os
import subprocess
import sys
import re
import streamlit as st
from . import APP_ROOT
from .conversation import (Conversation, answer_markdown, change_draft, prepare_child,
                           read_task, is_condition_change)
from .conversation_live import followup_quote
from .engine import online_quote
from .inputs import goals_summary
from .jobs import create_research, launch
from .presentation import candidate_cards
from .ui_home import show_quote


def stage_draft(task, draft):
    st.session_state['research_change_draft'] = draft
    st.session_state.pop('prepared', None)


def child_editor(task, storage):
    draft = st.session_state.get('research_change_draft')
    if not draft or draft['parent_task_id']!=task['id']:
        return
    prefix = 'child_'+task['id']+'_'+str(abs(hash(draft['question'])))
    st.subheader('新研究任务草稿')
    st.info('原研究保持不变。下方继承原条件，并填入能解析的改动；请核对全部条件，清空表示新任务不限制该项。')
    for note in draft['notes']:
        st.caption(note)
    values = draft['goals']
    with st.form(prefix):
        natural = st.text_area('新研究问题', draft['question'], key=prefix+'_question')
        def number(key, label, column=st):
            value = values[key]
            text = column.text_input(label, value='' if value is None else f'{value:g}', key=prefix+'_'+key)
            return text
        a,b = st.columns(2)
        raw = {k:number(k,label,col) for k,label,col in (
            ('temperature_min_K','新温度下限 / K',a),('temperature_max_K','新温度上限 / K',b),
            ('delta_E_kV_cm','电场变化幅度 ΔE（kV/cm）',a),('delta_t_K','新 ΔT 目标 / K',b))}
        with st.expander('高级电场条件', expanded=False):
            c,d = st.columns(2)
            raw.update({k:number(k,label,col) for k,label,col in (
                ('e_start_kV_cm','新电场起点 / kV/cm',c),('e_end_kV_cm','新电场终点 / kV/cm',d),
                ('e_min_kV_cm','新电场下限 / kV/cm',c),('e_max_kV_cm','新电场上限 / kV/cm',d))})
        st.caption('场起终点、场范围和 ΔE 各有含义；不自动补零，也不把上限换成 ΔE。温度可单边、负摄氏度对应值或小数。')
        include = a.text_input('新任务包含元素', ', '.join(values['include_elements']), key=prefix+'_include')
        exclude = b.text_input('新任务排除元素', ', '.join(values['exclude_elements']), key=prefix+'_exclude')
        material = st.text_input('新材料类型（选填）', values['material_type'] or '', key=prefix+'_material')
        ops = ['未指定','>','>=','<','<=','=']
        op = a.selectbox('新 ΔT 目标关系', ops, index=ops.index(values['delta_t_operator'] or '未指定'), key=prefix+'_op')
        maximize = b.checkbox('新任务偏好尽可能大的 ΔT', value=values['maximize'], key=prefix+'_maximize')
        mode = st.radio('新任务执行方式', ['本地研究 · 无需联网','DeepSeek 在线研究 · 需确认用量'], key=prefix+'_mode')
        review = st.form_submit_button('核对新任务条件', type='primary')
    if review:
        try:
            from ecalx.parsing.units import finite_number
            goals = {k:finite_number(v,k) if v.strip() else None for k,v in raw.items()}
            if goals['delta_E_kV_cm'] is not None and goals['delta_E_kV_cm'] <= 0:
                raise ValueError('电场变化幅度 ΔE 必须为正数；不限制时请留空')
            goals.update(include_elements=[s for s in re.split(r'[,，、\s]+',include.strip()) if s],
                exclude_elements=[s for s in re.split(r'[,，、\s]+',exclude.strip()) if s],
                material_type=material.strip() or None, delta_t_operator=None if op=='未指定' else op,
                maximize=maximize)
            if not natural.strip() or len(natural)>2000:
                raise ValueError('请填写1–2000字符的新研究问题')
            prepared = prepare_child(task, natural, goals)
            st.session_state[prefix+'_prepared'] = {'prepared':prepared,'mode':'live' if mode.startswith('DeepSeek') else 'local',
                'form_values':[natural,raw,include,exclude,material,op,maximize,mode]}
            st.session_state[prefix+'_confirmed'] = False
            st.session_state[prefix+'_paid'] = False
        except ValueError as e:
            st.session_state.pop(prefix+'_prepared',None)
            st.error(str(e))
    checked = st.session_state.get(prefix+'_prepared')
    # Form values returned on rerun invalidate stale confirmations even before submit.
    if checked and checked['form_values'] != [natural,raw,include,exclude,material,op,maximize,mode]:
        st.session_state.pop(prefix+'_prepared',None)
        checked = None
    if checked:
        st.write('**将创建独立新任务，原研究不变：**')
        for line in checked['prepared']['summary']:
            st.write(line)
        confirmed = st.checkbox('我确认新任务的全部条件', key=prefix+'_confirmed')
        consent, ready = None, True
        if checked['mode']=='live':
            try:
                q=online_quote()
                show_quote(q)
                paid = st.checkbox('我同意这个新任务的在线预算和数据发送',key=prefix+'_paid')
                consent, ready = (q if paid else None), paid
            except Exception:
                ready=False
                st.warning('当前在线配置、用量额度不可用或存在未知用量，未联网。可选择本地科研工具。')
        if st.button('创建新的研究任务', type='primary', disabled=not(confirmed and ready),key=prefix+'_create'):
            try:
                child=create_research(checked['prepared'],checked['mode'],confirmed=True,consent=consent,storage=storage)
                st.session_state.task_id=child['id']
                st.query_params['task']=child['id']
                st.session_state.pop('research_change_draft',None)
                launch(child['id'],storage)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if st.button('取消草稿，返回原研究',key=prefix+'_cancel'):
        st.session_state.pop('research_change_draft',None)
        st.rerun()


def render_turn(task, turn):
    with st.chat_message('user'):
        st.write(turn['question'])
    with st.chat_message('assistant'):
        label={'LOCAL_EVIDENCE_TOOLS':'本地资料解读',
            'LIVE_LLM':'DeepSeek 在线解读', 'CONDITION_CHANGE':'修改研究条件'}[turn['mode']]
        st.caption(label)
        if turn['status'] in {'RUNNING','PENDING'}:
            st.info('追问执行中；不会自动重复发送。')
            if st.button('刷新追问状态',key='refresh_'+turn['id']):
                st.rerun()
        else:
            answer=answer_markdown(turn)
            body,separator,sources=answer.partition('\n\n来源：')
            sections=re.split(r'\n\n(?=\*\*[^*]+\*\*\n)',body)
            st.markdown(sections[0])
            for section in sections[1:]:
                heading,_,details=section.partition('\n\n')
                with st.expander('补充证据 · '+heading.strip('*')):
                    st.markdown(details)
            if separator:
                st.markdown('来源：'+sources)
        if turn['status']=='NEEDS_NEW_TASK' and st.button('打开新任务草稿',key='draft_'+turn['id']):
            stage_draft(task,turn['draft'])
            st.rerun()
        with st.expander('本轮来源与记录'):
            st.json(turn)


def conversation_panel(task, storage):
    tid=task['id']
    conversation=Conversation(storage)
    st.subheader('追问研究结果')
    st.caption('修改研究条件时，将核对并创建新的研究。')
    history=conversation.history(tid)
    if history:
        for turn in history:
            render_turn(task,turn)
    cards=candidate_cards(task)
    labels={c['id']:c['formula'] for c in cards}
    with st.expander('追问选项 · 关注候选与回答方式'):
        selected=st.multiselect('本轮关注的候选（选填，最多两个）',list(labels),format_func=labels.get,
            max_selections=2,key='chat_candidates_'+tid)
        mode=st.radio('追问方式',['本地证据解释 · 免费','DeepSeek 在线解读 · 每轮确认用量'],
            key='chat_mode_'+tid,horizontal=True)
        st.caption('例如：为什么生成第一个候选？哪些目标条件仍缺少实验支持？')
    question=st.chat_input('继续问材料、候选或证据…',key='chat_input_'+tid,max_chars=2000)
    if question:
        try:
            # Host routes changes before pricing, credentials or provider construction.
            if is_condition_change(question) or mode.startswith('本地'):
                turn=conversation.create(tid,question,selected=selected)
                if turn['status']=='NEEDS_NEW_TASK':
                    stage_draft(task,turn['draft'])
            else:
                st.session_state['paid_followup_'+tid]={'question':question,'selected':selected}
                st.session_state['chat_paid_'+tid] = False
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    pending=st.session_state.get('paid_followup_'+tid)
    if pending:
        st.write('待发送追问：'+pending['question'])
        st.caption('本轮候选：'+('、'.join(labels[c] for c in pending['selected']) or '根据本条问题和此前指代读取'))
        try:
            q=followup_quote()
            show_quote(q)
            consent=st.checkbox('我同意仅此轮 DeepSeek 预算与发送范围',key='chat_paid_'+tid)
            if st.button('确认预算并发送本轮追问',disabled=not consent,key='chat_send_'+tid):
                turn=conversation.create(tid,pending['question'],mode='live',selected=pending['selected'],consent=q)
                st.session_state.pop('paid_followup_'+tid,None)
                st.session_state.pop('chat_paid_'+tid,None)
                subprocess.Popen([sys.executable,'-m','ecalx_local.conversation_worker',tid,turn['id'],str(storage)],
                    cwd=APP_ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                st.rerun()
        except Exception:
            st.warning('当前在线配置、用量额度不可用或存在未知用量。未发送请求；可改用本地证据解释。')
        if st.button('改用本地证据解释',key='chat_local_'+tid):
            conversation.create(tid,pending['question'],selected=pending['selected'])
            st.session_state.pop('paid_followup_'+tid,None)
            st.rerun()
        if st.button('取消本轮在线追问',key='chat_cancel_'+tid):
            st.session_state.pop('paid_followup_'+tid,None)
            st.rerun()
    if st.button('调整条件，创建新研究',key='chat_new_'+tid):
        stage_draft(task,change_draft(task,'继续探索电卡材料，调整研究条件'))
        st.rerun()
    if history:
        st.download_button('下载本次追问记录',json.dumps(history,ensure_ascii=False,indent=2),
            tid+'-conversation.json','application/json',key='chat_download_'+tid)
    child_editor(task,storage)
