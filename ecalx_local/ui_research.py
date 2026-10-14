"""Research surface: original execution controls and receipt-backed result views."""
import json
from . import PERSONAL
import streamlit as st
from .jobs import launch, worker_status
from .presentation import (TOOL_LABELS, STATUSES, candidate_cards, evidence_rows,
                           report_markdown, doi_link, protocol_section)
from .conditions import receipt_conditions, summary_lines, coverage_text, ZERO_NOTICE, evidence_notice
from .inputs import goals_summary
from .field_protocol import protocol_lines, imported_protocol
from .exploratory_ui import estimator
from .ui import section_header, task_status, formula_title, badge, science_note


def downloads(task, agent):
    st.write('研究需求、冻结条件、候选依据和文献来源汇总为中文报告。')
    a,b=st.columns(2)
    a.download_button('下载研究报告 · Markdown',report_markdown(task),task['id']+'.md','text/markdown',
                      use_container_width=True,key='report_'+task['id'])
    b.download_button('下载结构化结果 · JSON',json.dumps(agent.public(task),ensure_ascii=False,indent=2),
                      task['id']+'.json','application/json',use_container_width=True,key='json_'+task['id'])
    with st.expander('高级详情 · 科研收据、原始状态与来源版本'):
        st.caption('call_id、JSON Pointer 和哈希用于核查；以下内容来自实际任务。')
        st.json(agent.public(task))


def candidates(task, cards):
    st.caption('按原研究队列展示，不按性能或探索估计排序。点击详情查看证据和可选估计。')
    for i,card in enumerate(cards):
        if i%2==0:
            columns=st.columns(2,gap='medium')
        with columns[i%2].container(border=True):
            st.markdown('<div class="card-meta">'+badge(f"候选 {i+1:02d}")
                +badge('待验证 · generated_unverified')
                +badge('化学校验通过' if card['chemical_check'] else '化学校验待执行',
                       'teal' if card['chemical_check'] else 'neutral')+'</div>',unsafe_allow_html=True)
            formula_title(card['formula'])
            st.markdown('<div class="card-performance"><span>正式 ΔT <strong>unknown</strong></span>'
                '<span>正式 ΔS <strong>unknown</strong></span></div>',unsafe_allow_html=True)
            st.write(card['reason'])
            if card['condition_warning']:
                st.caption('目标条件证据：无匹配实验依据。' if card['condition_warning']==ZERO_NOTICE
                           else card['condition_warning'])
            st.caption('科学限制：'+card['gaps'])
            for r in card['sources'][:1]:
                st.markdown('[种子来源 · '+(r.get('doi') or r.get('url') or '无来源')+']('+(doi_link(r['doi']) if r.get('doi') else r.get('url') or '')+')')
            with st.expander('目标条件证据与组成详情'):
                st.write(coverage_text(card['seed_coverage']))
                st.caption('覆盖仅属于文献种子；未指定项不作条件。种子命中不等于候选达标，单点不保证整个温区。')
                st.write('独立化学校验：'+('通过；仅代表规则检查，不代表可合成或性能验证。' if card['chemical_check'] else '等待执行。'))
                for r in card['sources'][1:]:
                    st.markdown('[种子来源 · '+(r.get('doi') or r.get('url') or '无来源')+']('+(doi_link(r['doi']) if r.get('doi') else r.get('url') or '')+')')
            if PERSONAL:
              with st.expander('探索性 ΔT 估计 · 可选 / RESEARCH_ONLY'):
                  from ecalx.agent.contracts import Goals
                  estimator('dt_'+task['id']+'_'+card['id'],formula=card['formula'],
                            request=Goals(**task['frozen_goals']).request(),parent_task_id=task['id'],
                            parent_protocol=task.get('input_snapshot',{}).get('field_protocol'))
    if not cards:
        st.info('本次尚无可展示的新组成。可以查看文献依据和证据缺口。')


def evidence_panel(task, rows):
    summary=receipt_conditions(task)
    st.subheader('目标条件的证据覆盖')
    for line in protocol_lines(task.get('input_snapshot',{}).get('field_protocol')):
        st.write(line)
    if summary:
        a,b,c=st.columns(3)
        a.metric('符合温度条件','未指定' if summary['temperature_matches'] is None else summary['temperature_matches'])
        b.metric('符合电场条件','未指定' if summary['field_matches'] is None else summary['field_matches'])
        c.metric('温度 / 电场联合匹配','未指定' if summary['joint_matches'] is None else summary['joint_matches'])
        st.caption(f"全库 {summary['library_records']:,} 条；全部已指定条件匹配 {summary['all_goal_matches']} 条；完整协议 {summary['complete_protocol_matches']} 条，协议不完整 {summary['incomplete_protocol_matches']} 条。")
        from .evidence_display import amplitude_metrics
        amplitude_metrics(summary)
        if summary['all_goal_matches']==0:
            if summary.get('amplitude_only_auxiliary_matches',0) or summary.get('verified_path_matches',0):
                st.warning(evidence_notice(summary))
            else:
                st.error(ZERO_NOTICE)
        with st.expander('计数口径与协议缺口'):
            for line in summary_lines(summary)[3:]:
                if line!=ZERO_NOTICE:
                    st.write(line)
    else:
        st.caption('检索尚未执行，或历史未保存计数快照。未用当前库回填历史结果。')
    st.caption('温度和电场筛选文献、影响种子优先级；生成器另读全库，未计算候选在目标条件下的物性。')
    if rows:
        with st.expander('文献证据与实测条件（不同协议分别解释）', expanded=False):
            for r in rows[:8]:
                st.markdown('**'+r['formula_normalized']+'** · '+({'measured':'条件匹配记录', 'source_reported_data':'有效原文报告数据', 'amplitude_match_path_unconfirmed':'幅度匹配、路径未确认'}.get(r['presentation_status'],'原文数据 · 条件待补全')))
                if r.get('match_warning'):
                    st.caption(r['match_warning'])
                temp = '未知' if r['temperature_K'] is None else f"{r['temperature_K']:g} K"
                dt = '未知' if r['delta_T_ad_K'] is None else f"{r['delta_T_ad_K']:g} K"
                st.write(f"原文报告/换算 ΔT：{dt}；T：{temp}；方法：{r['measurement_method']}；形态：{r['sample_form']}。")
                estart = '未知' if r['e_start_kV_cm'] is None else f"{r['e_start_kV_cm']:g}"
                eend = '未知' if r['e_end_kV_cm'] is None else f"{r['e_end_kV_cm']:g}"
                ereported = '未知' if r['e_reported_kV_cm'] is None else f"{r['e_reported_kV_cm']:g}"
                st.caption(f"电场起点/终点：{estart} → {eend} kV/cm；原文场：{ereported} kV/cm / {r['e_reported_kind']}。")
                st.caption(f"独立字段：ΔE {r.get('e_delta_kV_cm')} kV/cm；电场上限 {r.get('e_max_kV_cm')} kV/cm（None表示未提供）。")
                st.caption(f"协议未知 / 未独立核验 · UNKNOWN；提供的实际峰值：{imported_protocol(r)['peak_kV_cm']} kV/cm。")
                st.caption(f"ΔT口径：{r['delta_t_basis']}；符号：{r['delta_t_sign_convention']}。来源种子数据不代表候选实测。")
                st.markdown('[用户来源]('+(doi_link(r['doi']) if r.get('doi') else r.get('url') or '')+') · '+r['original_row'])
                st.caption('待核验：'+'、'.join(r['quality_flags']))
            st.caption('完整本次展示记录及版本可下载研究报告。')
    calls = [c for c in task['calls'] if c['name']=='search_material_evidence' and c['output']['status']=='OK']
    if calls:
        from .evidence_display import statistics_panel
        statistics_panel(calls[-1]['output']['data'])
    protocol = protocol_section(task)
    if protocol:
        with st.expander('独立协议证据区 · 单论文正温降幅值'):
            st.write(protocol['notice'])
            if protocol['status']=='verified_readonly':
                st.write('已核实实验协议 · OBSERVED：核验只覆盖下列记录与字段，不扩展到用户参考或全库。')
                st.write(f"已只读核验 {len(protocol['records'])} 条 / {protocol['papers']} 篇；全部已指定条件匹配 {protocol['matching_records']} 条。不并入上方默认库计数，不参与候选性能或排名。")
                st.dataframe([{'组成':r['formula'], '温度 K':r['temperature_K'],
                    '场路径 kV/cm':f"{r['verified_fields']['e_start_kV_cm']:g}→{r['verified_fields']['e_end_kV_cm']:g}",
                    '正温降幅值 K':f"{r['positive_drop_magnitude_K']:.3f}",
                    '目标测试点':'匹配' if not r['unmet_conditions'] else '不匹配：'+','.join(r['unmet_conditions']),
                    '带符号ΔT':'unknown'} for r in protocol['records']], hide_index=True)
                st.markdown('[核验论文]('+doi_link(protocol['records'][0]['doi'])+')')
                st.caption('303 K与图中名义30°C有0.15 K舍入差；视觉曲线一致性核对，未独立重新数字化。完整记录定位与SHA见下载结果。')
    if task['virtual_ids']:
        st.info('本任务含实际运行的虚拟实验。模拟值和历史回放不构成候选材料的实测性能。')
        with st.expander('虚拟工具结果'):
            for call in task['calls']:
                if call['name']=='compare_optimization_strategies' and call['output']['status']=='OK':
                    st.dataframe(call['output']['data']['summary'], hide_index=True)


def execution(task, agent, storage):
    tid=task['id']
    work=worker_status(tid,storage)
    labels=[TOOL_LABELS.get(c['name'],c['name']) for c in task['calls'] if c['output']['status']=='OK']
    planned=len(task['plan']['steps']) if task['plan'] else 0
    done=len({c['name'] for c in task['calls'] if c['output']['status']=='OK'})
    if task['status'] not in {'COMPLETED','FAILED','CANCELLED'}:
        st.subheader('研究计划待确认' if task['status']=='DRAFT' and task['plan'] else STATUSES[task['status']])
        st.progress(min(.9,done/max(planned+1,1)),text=labels[-1] if labels else ('等待科研工具返回结果' if task['status']=='RUNNING' else '正在准备计划和工具'))
        st.caption(f'已完成 {len(labels)} 次工具调用 · 页面自动更新')
    if task['plan']:
        with st.expander('研究计划',expanded=task['status']=='DRAFT'):
            for step in task['plan']['steps']:
                st.write('• '+TOOL_LABELS.get(step,step))
    if task['status']=='DRAFT' and task['plan']:
        st.info('计划已生成。确认后开始执行科研工具。')
        if task['client_mode']=='LIVE_LLM':
            st.caption('原共享 API 任务时限包含计划后的等待时间；若已过期请新建任务。')
        if st.button('确认计划并执行',type='primary'):
            agent.confirm_plan(tid,confirmed=True,accept_unquantified=True)
            launch(tid,storage)
            st.rerun(scope='fragment')
    if task['status']=='AWAITING_APPROVAL':
        preview=task['pending']['preview']
        st.warning('即将运行虚拟实验。结果仅代表模拟/历史环境，需确认本次条件与查询预算。')
        params=preview.get('parameters',{})
        st.write(f"环境：{params.get('adapter')}；温度 {params.get('temperature_K')} K；电场 {params.get('e_start_kV_cm')} → {params.get('e_end_kV_cm')} kV/cm。")
        st.write(f"本次查询上限：{preview.get('budget',{}).get('total_queries','在已冻结预算内')}。未填写目标仍保持未指定。")
        with st.expander('虚拟实验参数与候选池'):
            st.json(preview)
        if st.button('确认本次虚拟操作并继续',type='primary'):
            agent.approve(tid,task['pending']['fingerprint'],confirmed=True)
            launch(tid,storage)
            st.rerun(scope='fragment')
    a,b,c=st.columns(3)
    if task['status'] in {'RUNNING','AWAITING_APPROVAL'} and a.button('暂停任务',use_container_width=True):
        agent.control(tid,'PAUSE');st.rerun(scope='fragment')
    if task['status']=='PAUSED' and a.button('继续研究',use_container_width=True):
        agent.control(tid,'RESUME');launch(tid,storage);st.rerun(scope='fragment')
    if task['status'] not in {'COMPLETED','FAILED','CANCELLED'} and b.button('停止任务',use_container_width=True):
        agent.control(tid,'CANCEL');st.rerun(scope='fragment')
    if work['state'] in {'idle','stopped','interrupted'} and task['status']=='RUNNING' and c.button('继续未结束的任务',use_container_width=True):
        launch(tid,storage)
    if work['state']=='interrupted':
        st.warning('任务进程中断；阶段结果已保留。在途在线请求按原策略核对未知用量，不自动重试。')
    if task['status'] in {'RUNNING','DRAFT','PAUSED','CANCELLED'}:
        st.caption('暂停/停止在在途请求或原子工具结束后生效；已发生的在线费用不能撤销。')
    if task['status']=='FAILED':
        st.error('执行未完成，阶段结果已保留。可查看报告或高级详情了解原因；不会自动重试。')


def research(agent, storage, open_history):
    tid=st.session_state.task_id
    initial=agent.get(tid)
    section_header('研究工作区', '材料研究')
    task_status(initial)
    with st.chat_message('user'):
        st.write(initial['original'] or '开放探索 · 未指定研究条件')
    with st.expander('已冻结的研究条件'):
        for line in goals_summary(initial['frozen_goals']):
            st.write(line)
        for pref in initial.get('optional_preferences',[]):
            st.caption(pref)
        st.caption('调整条件需创建新研究并重新确认。')
    if initial.get('parent_research'):
        if st.button('返回来源研究',key='parent_'+tid):
            open_history(initial['parent_research']['task_id']);st.rerun()
    terminal={'COMPLETED','FAILED','CANCELLED'}
    @st.fragment(run_every=None if initial['status'] in terminal else '2s')
    def task_panel():
        task=agent.get(tid)
        if task['status'] != initial['status']:
            st.rerun()
        with st.chat_message('assistant'):
            st.caption('ECalX · '+('真实 DeepSeek 研究' if task['client_mode']=='LIVE_LLM' else '本地科研工具 · 确定性流程，未调用 LLM'))
            execution(task,agent,storage)
            cards,rows=candidate_cards(task),evidence_rows(task)
            if task['status']=='COMPLETED':
                st.markdown(f'已整理 **{len(cards)} 个待验证组成**和 **{len(rows)} 条文献记录**。可以展开候选依据，或继续追问这次研究。')
            if cards or rows or task['status'] in terminal:
                science_note()
                summary=receipt_conditions(task)
                if summary is not None and summary['all_goal_matches']==0:
                    if summary.get('amplitude_only_auxiliary_matches',0) or summary.get('verified_path_matches',0):
                        st.warning(evidence_notice(summary))
                    else:
                        st.error(ZERO_NOTICE)
                a,b,c=st.columns(3)
                a.metric('待验证组成',len(cards));b.metric('文献记录',len(rows));c.metric('正式 ΔT / ΔS','unknown')
                tab1,tab2,tab3=st.tabs(['候选材料','文献与条件','研究报告'])
                with tab1:
                    candidates(task,cards)
                with tab2:
                    evidence_panel(task,rows)
                with tab3:
                    downloads(task,agent)
    task_panel()
    if initial['status']=='COMPLETED':
        # Keep the composer at page root so Streamlit pins it to the viewport.
        from .conversation_ui import conversation_panel
        conversation_panel(initial,storage)
