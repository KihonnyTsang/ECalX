"""Research surface: original execution controls and receipt-backed result views."""
import json
from .titles import research_title
from . import PERSONAL
import streamlit as st
from .jobs import launch, worker_status
from .presentation import (TOOL_LABELS, STATUSES, candidate_cards, evidence_rows,
                           report_markdown, doi_link, protocol_section)
from .conditions import receipt_conditions, summary_lines, coverage_text, ZERO_NOTICE
from .inputs import goals_summary
from .field_protocol import protocol_lines, imported_protocol
from .ui import section_header, task_status, formula_title, badge, science_note, protocol_summary, evidence_message


def downloads(task, agent):
    a,b=st.columns(2)
    tid=task['id']
    a.download_button('下载研究报告',lambda:report_markdown(agent.get(tid)),tid+'.md','text/markdown',
                      use_container_width=True,key='report_'+tid,on_click='ignore')
    b.download_button('下载原始数据',lambda:json.dumps(agent.public(agent.get(tid)),ensure_ascii=False,indent=2),
                      tid+'.json','application/json',use_container_width=True,key='json_'+tid,on_click='ignore')
    detail=st.expander('运行记录与来源版本',on_change='rerun',key='receipts_'+tid)
    if detail.open:
      with detail:
        st.caption('数据版本：'+str(task.get('data_version','未提供')))
        st.dataframe([{'工具':TOOL_LABELS.get(c['name'],c['name']),
                       '状态':c['output']['status'],'收据':c['id']} for c in task['calls']],hide_index=True)


def simulation_conditions(run):
    p=run['report']['experiment']['conditions']
    return f"模拟条件：{p['temperature_K']:g} K · {p['e_start_kV_cm']:g} → {p['e_end_kV_cm']:g} kV/cm"


def candidate_evidence(card, estimate, score=None):
    """Keep provenance and full precision behind an explicit details entry."""
    literature,primary=estimate['literature'],estimate['primary']
    st.write('参考组成：'+card['seed_formula'])
    for r in card['sources']:
        url=doi_link(r['doi']) if r.get('doi') else r.get('url')
        if url:
            st.markdown('[来源 · '+(r.get('doi') or r['url'])+']('+url+')')
    if literature['point']:
        p=literature['point']
        st.markdown('[插值文献 · Dai · '+p['source_doi']+']('+p['source_url']+')')
        st.write(literature['reason'])
        st.write(p['protocol_note'])
        st.write('适用域：BST 块体陶瓷；候选实际样品形态尚未验证。')
        st.write('温度覆盖：'+str(p['temperature_coverage_K'])+' K；'+p['result_description'])
        st.write('参考组成及权重：'+'；'.join(f"x={r['sr_molar_fraction']:g}，{r['composition_weight']:.0%}，图{r['figure']}" for r in p['reference_sources']))
        st.code('文献插值 ΔT（K）：'+repr(p['delta_T_magnitude_K']),language=None)
        st.caption('数据 SHA256：'+p['source_csv_sha256'])
        st.caption(p['numerical_precision_note'])
    else:
        st.write('文献插值：'+literature['reason'])
    st.write('RF/XGBoost：'+estimate['model']['reason'])
    if primary:
        st.code('研究性估计 ΔT（K）：'+repr(primary['prediction_K']),language=None)
        st.write(primary['inputs'])
        st.caption('模型收据：'+primary['receipt_id']+'；模型 SHA256：'+primary['metadata']['model_sha256'])
    if score:
        st.code('模拟评分：'+repr(score['模拟评分']),language=None)
    st.write(card['reason'])
    if card['condition_warning']:
        st.write(card['condition_warning'])
    st.write('待验证：'+card['gaps'])
    st.write(coverage_text(card['seed_coverage']))
    st.write('独立化学检查：'+('通过；规则检查不代表可合成或性能验证。' if card['chemical_check'] else '待执行。'))


def candidates(task, cards):
    from .element_research import ranking
    from .candidate_delta_t import candidate_results
    run=(task.get('simulation_runs') or [None])[-1]
    ranked=ranking(run) if run else []
    scores={r['candidate_id']:r for r in ranked}
    ordered=sorted(cards,key=lambda c:scores.get(c['id'],{}).get('名次',len(cards)+1))
    page_key='candidate_page_'+task['id']
    pages=max(1,(len(cards)+3)//4)
    page=min(max(1,st.session_state.get(page_key,1)),pages)
    visible=ordered[(page-1)*4:page*4]
    estimates=candidate_results(task,visible)
    for i,card in enumerate(visible):
        if i%2==0:
            columns=st.columns(2,gap='medium')
        score=scores.get(card['id'])
        with columns[i%2].container(border=True,key='material_card_'+card['id']):
            if score:
                st.markdown('<div class="card-meta">'+badge(f"模拟排名 #{score['名次']}",'teal' if score['名次']==1 else 'neutral')+'</div>',unsafe_allow_html=True)
            formula_title(card['formula'])
            estimate=estimates[card['id']]
            literature,primary=estimate['literature'],estimate['primary']
            values=[]
            if literature['point']:
                values.append(('文献插值 ΔT',f"{literature['point']['delta_T_magnitude_K']:.3f} K"))
            if primary:
                values.append(('研究性估计 ΔT',f"{primary['prediction_K']:.3f} K"))
            if score:
                values.append(('模拟评分',f"{score['模拟评分']:.3f}"))
            if values:
                for area,(label,value) in zip(st.columns(len(values)),values):
                    area.metric(label,value)
            if literature['point']:
                p=literature['point']
                st.caption(f"{p['temperature_K']:g} K · 文献撤场 {p['nominal_delta_E_kV_cm']:g} kV/cm")
            elif primary:
                p=primary['inputs']
                st.caption(f"{p['temperature_K']:g} K · ΔE {p['delta_E_kV_cm']:g} kV/cm")
            elif score:
                st.caption(simulation_conditions(run))
            detail=st.expander('查看详情',on_change='rerun',key='candidate_details_'+card['id'])
            if detail.open:
                with detail:
                    candidate_evidence(card,estimate,score)
                    if PERSONAL:
                        from .exploratory_ui import estimator
                        from ecalx.agent.contracts import Goals
                        estimator('dt_'+task['id']+'_'+card['id'],formula=card['formula'],
                                  request=Goals(**task['frozen_goals']).request(),parent_task_id=task['id'],
                                  parent_protocol=estimate['conditions']['field_protocol'])
    if pages>1:
        st.number_input('候选页码',min_value=1,max_value=pages,value=page,step=1,key=page_key)


def detailed_results(task, cards, agent, storage):
    from .element_research import ranking
    run=(task.get('simulation_runs') or [None])[-1]
    st.subheader('详细结果')
    detail=st.expander('图表、来源与运行记录',on_change='rerun',key='result_details_'+task['id'])
    if detail.open:
        with detail:
            if run:
                st.caption(simulation_conditions(run))
                st.bar_chart([{'组成':r['候选组分'],'模拟评分':r['模拟评分']} for r in ranking(run)],x='组成',y='模拟评分',height=180)
            evidence_panel(task,evidence_rows(task))
            downloads(task,agent)


def _evidence_details(task, rows):
    summary=receipt_conditions(task)
    st.subheader('目标条件的证据覆盖')
    st.caption(protocol_summary(task.get('input_snapshot',{}).get('field_protocol')))
    if summary:
        a,b,c=st.columns(3)
        a.metric('符合温度条件','未指定' if summary['temperature_matches'] is None else summary['temperature_matches'])
        b.metric('符合电场条件','未指定' if summary['field_matches'] is None else summary['field_matches'])
        c.metric('温度 / 电场联合匹配','未指定' if summary['joint_matches'] is None else summary['joint_matches'])
        st.caption(f"文献库共 {summary['library_records']:,} 条，符合全部筛选条件 {summary['all_goal_matches']} 条。")
        from .evidence_display import amplitude_metrics
        amplitude_metrics(summary)
        if summary['all_goal_matches']==0:
            if summary.get('amplitude_only_auxiliary_matches',0) or summary.get('verified_path_matches',0):
                st.warning(evidence_message(summary))
            else:
                st.warning(evidence_message(summary))
        with st.expander('高级详情 · 匹配统计与实验条件'):
            for line in protocol_lines(task.get('input_snapshot',{}).get('field_protocol')):
                st.write(line)
            st.caption(f"完整实验条件 {summary['complete_protocol_matches']} 条，条件不完整 {summary['incomplete_protocol_matches']} 条。")
            for line in summary_lines(summary)[3:]:
                if line!=ZERO_NOTICE:
                    st.write(line)
    else:
        st.caption('本次研究暂无匹配统计。')
    with st.expander('帮助说明 · 研究条件如何影响候选'):
        st.write('温度和电场条件用于筛选文献，并影响参考材料的优先顺序。候选生成仍会参考整个文献库，尚未预测候选在目标条件下的性能。')
    if rows:
        with st.expander('文献证据与实测条件（不同协议分别解释）', expanded=False):
            for r in rows[:8]:
                st.markdown('**'+r['formula_normalized']+'** · '+({'measured':'条件匹配记录', 'source_reported_data':'有效原文报告数据', 'amplitude_match_path_unconfirmed':'幅度匹配、路径未确认'}.get(r['presentation_status'],'原文数据 · 条件待补全')))
                if r.get('match_warning'):
                    st.caption(r['match_warning'])
                temp = '未知' if r['temperature_K'] is None else f"{r['temperature_K']:g} K"
                dt = '未知' if r['delta_T_ad_K'] is None else f"{r['delta_T_ad_K']:g} K"
                st.write(f"原文报告/换算 ΔT：{dt}；T：{temp}；方法：{r['measurement_method']}；形态：{r['sample_form']}。")
                if r.get('delta_T_jump_abs_K') is not None or r.get('delta_S_vol_abs_kJ_K_m3') is not None:
                    from .manual_ui import record_preview
                    st.dataframe([record_preview(r)],hide_index=True)
                    st.caption('独立实验响应；未换算为本征 ΔTad 或质量 ΔS，不作为候选性能排名依据。')
                estart = '未知' if r['e_start_kV_cm'] is None else f"{r['e_start_kV_cm']:g}"
                eend = '未知' if r['e_end_kV_cm'] is None else f"{r['e_end_kV_cm']:g}"
                ereported = '未知' if r['e_reported_kV_cm'] is None else f"{r['e_reported_kV_cm']:g}"
                st.caption(f"电场起点/终点：{estart} → {eend} kV/cm；原文场：{ereported} kV/cm / {r['e_reported_kind']}。")
                st.caption(f"独立字段：ΔE {r.get('e_delta_kV_cm')} kV/cm；电场上限 {r.get('e_max_kV_cm')} kV/cm（None表示未提供）。")
                st.caption(f"加电方式待核实；提供的实际峰值：{imported_protocol(r)['peak_kV_cm']} kV/cm。")
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
                st.write('已核实实验协议：核验只覆盖下列记录与字段，不扩展到用户参考或全库。')
                st.write(f"已只读核验 {len(protocol['records'])} 条 / {protocol['papers']} 篇；全部已指定条件匹配 {protocol['matching_records']} 条。不并入上方默认库计数，不参与候选性能或排名。")
                st.dataframe([{'组成':r['formula'], '温度 K':r['temperature_K'],
                    '场路径 kV/cm':f"{r['verified_fields']['e_start_kV_cm']:g}→{r['verified_fields']['e_end_kV_cm']:g}",
                    '正温降幅值 K':f"{r['positive_drop_magnitude_K']:.3f}",
                    '目标测试点':'匹配' if not r['unmet_conditions'] else '不匹配：'+','.join(r['unmet_conditions']),
                    '带符号ΔT':'未提供'} for r in protocol['records']], hide_index=True)
                st.markdown('[核验论文]('+doi_link(protocol['records'][0]['doi'])+')')
                st.caption('303 K与图中名义30°C有0.15 K舍入差；视觉曲线一致性核对，未独立重新数字化。完整记录定位与SHA见下载结果。')
    if task['virtual_ids']:
        st.info('本任务含实际运行的虚拟实验。模拟值和历史回放不构成候选材料的实测性能。')
        with st.expander('虚拟工具结果'):
            for call in task['calls']:
                if call['name']=='compare_optimization_strategies' and call['output']['status']=='OK':
                    st.dataframe(call['output']['data']['summary'], hide_index=True)


def evidence_panel(task, rows):
    summary=receipt_conditions(task)
    if summary:
        st.caption(f"符合全部条件 {summary['all_goal_matches']} 条 · 文献库 {summary['library_records']:,} 条")
        if summary['all_goal_matches']==0:
            st.caption(evidence_message(summary))
    if rows:
        method={'direct':'直接法','indirect':'间接法'}
        st.dataframe([{'材料组成':r['formula_normalized'],'温度（K）':r['temperature_K'],
            '原文 ΔT（K）':r['delta_T_ad_K'],
            '测量方法':method.get(r['measurement_method'],'未提供'),
            '条件状态':{'measured':'条件匹配','source_reported_data':'原文数据',
                'amplitude_match_path_unconfirmed':'ΔE 匹配，路径待确认'}.get(r['presentation_status'],'条件待补全'),
            '来源':doi_link(r['doi']) if r.get('doi') else r.get('url') or ''} for r in rows],
            hide_index=True,use_container_width=True,
            column_config={'来源':st.column_config.LinkColumn('来源',display_text='查看文献')})
    else:
        st.caption('暂无可展示的文献记录')
    with st.expander('来源与条件详情'):
        _evidence_details(task,rows)


def result_summary(task):
    cards,rows=candidate_cards(task),evidence_rows(task)
    with st.container(key='result_summary'):
        a,b,c=st.columns(3)
        a.metric('候选材料数量',len(cards))
        b.metric('文献记录数量',len(rows))
        c.metric('化学检查结果',f"{sum(bool(c['chemical_check']) for c in cards)} / {len(cards)}" if cards else '未执行')
    return cards,rows


def report_summary(task,agent):
    from .bst_ui import render_result
    st.subheader('研究结论')
    if task.get('input_snapshot',{}).get('allowed_elements'):
        from .element_ui import report_overview
        report_overview(task)
        downloads(task,agent)
        detail=st.expander('全部候选与文献',on_change='rerun',key='report_details_'+task['id'])
        if detail.open:
          with detail:
            candidates(task,candidate_cards(task))
            evidence_panel(task,evidence_rows(task))
        return
    for result in task.get('literature_results',[]):
        render_result(result)
    cards,rows=result_summary(task)
    if cards:
        candidates(task,cards)
    elif not task.get('literature_results'):
        st.caption('本次尚无候选材料或计算结果。' if task['status']!='COMPLETED' else '本次未生成候选材料。')
    with st.expander('文献与研究条件'):
        for line in goals_summary(task['frozen_goals']):
            st.write(line)
        evidence_panel(task,rows)
    with st.expander('导出与原始数据'):
        downloads(task,agent)


def execution(task, agent, storage):
    tid=task['id']
    work=worker_status(tid,storage)
    labels=[TOOL_LABELS.get(c['name'],c['name']) for c in task['calls'] if c['output']['status']=='OK']
    planned=len(task['plan']['steps']) if task['plan'] else 0
    done=len({c['name'] for c in task['calls'] if c['output']['status']=='OK'})
    if task['status'] not in {'COMPLETED','FAILED','CANCELLED'}:
        st.subheader('研究计划待确认' if task['status']=='DRAFT' and task['plan'] else STATUSES[task['status']])
        st.progress(min(.9,done/max(planned+1,1)),text=labels[-1] if labels else ('等待科研工具返回结果' if task['status']=='RUNNING' else '正在准备计划和工具'))
        st.caption(f'已完成 {len(labels)} 个步骤'+(' · 自动更新' if task['status']=='RUNNING' or not task.get('plan') else ' · 等待操作'))
    if task['plan']:
        with st.expander('研究计划',expanded=task['status']=='DRAFT'):
            for step in task['plan']['steps']:
                st.write('• '+TOOL_LABELS.get(step,step))
    if task['status']=='DRAFT' and task['plan']:
        st.info('计划已生成。确认后开始执行科研工具。')
        if task['client_mode']=='LIVE_LLM':
            st.caption('在线研究的时限包含等待确认的时间，过期后请新建研究。')
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
        st.warning('研究意外中断，已有结果已保存。在线请求用量待核对，不会自动重试。')
    if task['status'] in {'RUNNING','DRAFT','PAUSED','CANCELLED'}:
        st.caption('暂停或停止在当前步骤结束后生效。' + ('已产生的在线费用无法撤销。' if task['client_mode']=='LIVE_LLM' else ''))
    if task['status']=='FAILED':
        st.error('执行未完成，阶段结果已保留。可查看报告或高级详情了解原因；不会自动重试。')


def research(agent, storage, open_history):
    tid=st.session_state.task_id
    initial=agent.get(tid)
    section_header('', '材料研究')
    st.markdown('**'+research_title(initial)+'**')
    task_status(initial)
    with st.expander('研究条件'):
        from .input_display import compact_conditions
        if initial.get('input_snapshot',{}).get('raw',{}).get('composition'):
            st.write('材料组成：'+initial['input_snapshot']['raw']['composition'])
        if initial.get('input_snapshot',{}).get('allowed_elements'):
            st.write('允许元素：'+'、'.join(initial['input_snapshot']['allowed_elements']))
        for line in compact_conditions(initial['frozen_goals']):
            st.write(line)
    if initial.get('parent_research'):
        if st.button('返回来源研究',key='parent_'+tid):
            open_history(initial['parent_research']['task_id']);st.rerun()
    terminal={'COMPLETED','FAILED','CANCELLED'}
    polling=initial['status']=='RUNNING' or (initial['status']=='DRAFT' and not initial.get('plan'))
    @st.fragment(run_every='2s' if polling else None)
    def task_panel():
        task=agent.get(tid)
        if task['status']!=initial['status']:
            st.rerun()
        if task['status'] not in terminal:
            execution(task,agent,storage)
        elif task['status']=='FAILED':
            st.error('研究未完成，已有结果已保存。')
        cards,rows=candidate_cards(task),evidence_rows(task)
        if cards or rows or task['status'] in terminal:
            if task.get('input_snapshot',{}).get('allowed_elements'):
                from .element_ui import checks_panel, simulation_panel, optimization_panel, conclusion, report_overview
                tabs=st.tabs(['生成与校验','模拟计算','评价与排序','研究结果','报告与依据'],
                             default='研究结果',on_change='rerun',key='result_tab_'+tid)
                if tabs[0].open:
                    with tabs[0]: checks_panel(task)
                if tabs[1].open:
                    with tabs[1]: simulation_panel(task,agent)
                if tabs[2].open:
                    with tabs[2]: optimization_panel(task,agent)
                if tabs[3].open:
                    with tabs[3]:
                        st.subheader('结果概览')
                        result_summary(task)
                        st.subheader('候选材料')
                        candidates(task,cards)
                        detailed_results(task,cards,agent,storage)
                if tabs[4].open:
                  with tabs[4]:
                    report_overview(task)
                    downloads(task,agent)
                    detail=st.expander('文献证据与研究过程',on_change='rerun',key='evidence_'+tid)
                    if detail.open:
                      with detail:
                        evidence_panel(task,rows)
                        execution(task,agent,storage)
            else:
                tab1,tab2,tab3=st.tabs(['候选材料','文献依据','报告与详情'],on_change='rerun',key='result_tab_'+tid)
                if tab1.open:
                    with tab1:
                        st.subheader('结果概览')
                        result_summary(task)
                        if task.get('literature_results'):
                            from .bst_ui import research_tool
                            research_tool(task['input_snapshot'],agent=agent,task=task)
                        st.subheader('候选材料')
                        candidates(task,cards)
                        detailed_results(task,cards,agent,storage)
                if tab2.open:
                    with tab2: evidence_panel(task,rows)
                if tab3.open:
                  with tab3:
                    downloads(task,agent)
                    if task['status'] in terminal:
                        with st.expander('研究过程'):
                            execution(task,agent,storage)
    task_panel()
    if initial['status']=='COMPLETED':
        detail=st.expander('继续研究',on_change='rerun',key='conversation_'+tid)
        if detail.open:
          from .conversation_ui import conversation_panel
          with detail:
            conversation_panel(initial,storage)
