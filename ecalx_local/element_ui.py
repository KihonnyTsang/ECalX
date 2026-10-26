"""Receipt-backed views inside the existing workbench, history and reports."""
import pandas as pd
import streamlit as st
from .element_research import generation_result, create_simulation, advance_simulation, ranking
from .ui import formula_title


def checks_panel(task):
    generated=generation_result(task)
    checked={c['output']['data'].get('formula') for c in task['calls']
        if c['name']=='validate_chemical_constraints' and c['output']['status']=='OK' and c['output']['data'].get('chemical_pass')}
    st.dataframe([{'候选组成':c['formula_normalized'],'化学计量':'通过','电荷平衡':'通过',
        '元素约束':'通过','独立校验':'通过' if c['formula_raw'] in checked else '待执行'}
        for c in generated['candidates']],hide_index=True,use_container_width=True,height=390)
    with st.expander('生成依据与精确计量'):
        st.write('程序从来源种子枚举同价替代组成；5%–25% 为工程搜索网格。')
        st.write('位点、电荷检查基于声明价态；实际结构、可合成性与电卡性能尚未验证。')
        for c in generated['candidates']:
            st.write(c['formula_normalized']+' · '+str(c['stoichiometry_exact']))
        for seed in generated.get('seeds',[]):
            for r in seed['sources']:
                if r.get('doi'):
                    st.markdown(f"[种子文献 · {r['doi']}](https://doi.org/{r['doi']})")


def latest(task):
    return task.get('simulation_runs',[None])[-1]


def simulation_panel(task,agent):
    from ecalx.experiments.candidate_pool import generated_candidate_pool
    st.subheader('模拟评分')
    bridge=generated_candidate_pool(generation_result(task),'SIMULATED',agent.root)
    st.write(f"可模拟 {len(bridge['accepted'])} 个 · 域外 {len(bridge['rejected'])} 个")
    run=latest(task)
    with st.expander('模拟参数',expanded=not bool(run)):
        goals=task['frozen_goals']
        values=run['report']['experiment']['conditions'] if run else {
            'temperature_K':goals['temperature_min_K'] if goals['temperature_min_K']==goals['temperature_max_K'] else None,
            'e_start_kV_cm':goals['e_start_kV_cm'],'e_end_kV_cm':goals['e_end_kV_cm']}
        def text(key):
            return '' if values.get(key) is None else f'{values[key]:g}'
        with st.form('simulation_form_'+task['id']):
            a,b,c=st.columns(3)
            temp=a.text_input('模拟温度（K）',value=text('temperature_K'),key='sim_t_'+task['id'])
            start=b.text_input('模拟起点（kV/cm）',value=text('e_start_kV_cm'),key='sim_s_'+task['id'])
            end=c.text_input('模拟终点（kV/cm）',value=text('e_end_kV_cm'),key='sim_e_'+task['id'])
            confirmed=st.checkbox('确认以上温度与电场用于本次虚拟模拟',key='sim_confirm_'+task['id'])
            submitted=st.form_submit_button('运行模拟' if not run else '按新参数运行独立模拟',type='primary',disabled=len(bridge['accepted'])<3)
        if submitted:
            try:
                if not confirmed:
                    raise ValueError('请明确确认本次模拟条件；未指定的温度和场起止值不会自动补全。')
                if not all(v.strip() for v in (temp,start,end)):
                    raise ValueError('请填写模拟温度、电场起点和终点。')
                with st.spinner('正在计算…'):
                    create_simulation(agent,task,float(temp),float(start),float(end))
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if not run:
        return
    report=run['report'];p=report['experiment']['conditions'];rows=ranking(run)
    from .ui_research import simulation_conditions
    st.caption(simulation_conditions(run))
    st.dataframe([{k:r[k] for k in ('候选组分','模拟评分')} for r in rows],
                 hide_index=True,use_container_width=True,column_config={'模拟评分':st.column_config.NumberColumn(format='%.3f')})
    chart=st.expander('模拟曲线',on_change='rerun',key='simulation_chart_'+task['id'])
    if chart.open:
        with chart:
            model=report['models'][-1];observed={r['candidate_id']:r['value'] for r in report['observations']}
            plot=pd.DataFrame([{'Sr 位点分数':c['features'][0],'GP 研究性估计':model['predictions'][c['candidate_id']],
                               '已计算模拟评分':observed.get(c['candidate_id'])} for c in report['experiment']['candidates']]).set_index('Sr 位点分数')
            st.line_chart(plot,height=210)
    with st.expander('适用域与计算依据'):
        st.write('仅接受冻结 BST 网格的精确组成；含 Ca 或网格外组分不计算、不补值。')
        st.write('计算设置独立于研究目标。评分来自未校准模拟器，不代表实测电卡 ΔT。')
        st.write(f"已计算 {len(rows)} / {len(report['experiment']['candidates'])} 个组成；RBF 高斯过程拟合模拟反馈，后验未经校准。")
        st.code(p['simulator']['equation'],language=None,wrap_lines=True)
        st.write('maxwell-toy-1：Tc(x)、密度、热容与极化温变参数为演示假设。')
        ids={c['candidate_id']:c['formula_normalized'] for c in generation_result(task)['candidates']}
        st.dataframe([{'未模拟组分':ids[r['generated_candidate_id']],'原因':r['reason']} for r in bridge['rejected']],hide_index=True)


def optimization_panel(task,agent):
    st.subheader('模拟排名')
    run=latest(task)
    if not run:
        st.write('运行模拟后查看排名。')
        return
    report=run['report'];rows=ranking(run);n=len(report['experiment']['candidates'])
    from .ui_research import simulation_conditions
    st.caption(simulation_conditions(run))
    remaining=n-len(rows)
    a,b=st.columns(2)
    if a.button('评价下一候选',type='primary',disabled=not remaining,use_container_width=True):
        advance_simulation(agent,task,run);st.rerun()
    if b.button('完成剩余候选评价',disabled=not remaining,use_container_width=True):
        advance_simulation(agent,task,run,all_remaining=True);st.rerun()
    winner=rows[0]
    formula_title(winner['候选组分'])
    st.write(f"模拟排名 #1 · 模拟评分 **{winner['模拟评分']:.3f}**")
    st.dataframe([{k:r[k] for k in ('名次','候选组分','模拟评分')} for r in rows],hide_index=True,
                 use_container_width=True,column_config={'模拟评分':st.column_config.NumberColumn(format='%.3f')})
    trace=report['loop']['trace'];initial=max(o['value'] for o in report['experiment']['initial_feedback'])
    with st.expander('评价曲线与逐轮轨迹'):
        curve=pd.DataFrame([{'轮次':0,'当前最高模拟评分':initial}]+[{'轮次':r['round'],'当前最高模拟评分':r['cumulative_best']} for r in trace]).set_index('轮次')
        st.line_chart(curve,height=170)
        st.write('EI（期望改进）最大化模拟评分；每轮选点后执行模拟、追加反馈并重新拟合 GP。')
        st.write(f"已完成 {len(trace)} 轮 EI；已计算 {len(rows)} / {n} 个准入候选。按同一模拟环境的反馈降序；域外及未计算候选不参与排名。")
        st.write('结果仅适用于本次模拟设置；真实材料 ΔT、ΔS 与性能排名未知。')
        st.dataframe([{'轮次':r['round'],'本轮组成':r['formula'],'模拟反馈':r['feedback']['value'],
            '计算前估计':r['prediction_before'],'累计最高':r['cumulative_best'],
            '模型版本':r['model_after'].rsplit(':',1)[-1]} for r in trace],hide_index=True,
            column_config={k:st.column_config.NumberColumn(format='%.3f') for k in ('模拟反馈','计算前估计','累计最高')})


def conclusion(task):
    run=latest(task)
    if run:
        rows=ranking(run)
        formula_title(rows[0]['候选组分'])
        st.write(f"模拟排名 #1 · 模拟评分 **{rows[0]['模拟评分']:.3f}**")


def report_overview(task):
    from .input_display import compact_conditions
    from .ui_research import candidate_evidence,simulation_conditions
    from .presentation import candidate_cards
    from .candidate_delta_t import candidate_results
    st.subheader('研究摘要')
    st.write(task['original'])
    allowed=task.get('input_snapshot',{}).get('allowed_elements',[])
    if allowed: st.write('允许元素：'+'、'.join(allowed))
    for line in compact_conditions(task['frozen_goals']):
        st.write(line)
    conclusion(task)
    run=latest(task)
    scores={}
    if run:
        st.caption(simulation_conditions(run))
        scores={r['candidate_id']:r for r in ranking(run)}
    detail=st.expander('候选计算与来源',on_change='rerun',key='report_sources_'+task['id'])
    if detail.open:
        with detail:
            st.write('化学规则 chemical-rules-0.1.1；全部候选待实验验证。')
            cards=candidate_cards(task)
            estimates=candidate_results(task,cards)
            for card in cards:
                formula_title(card['formula'])
                candidate_evidence(card,estimates[card['id']],scores.get(card['id']))
    preferences=task.get('input_snapshot',{}).get('pending',[])+task.get('optional_preferences',[])
    if preferences:
        with st.expander('原始需求与解析记录'):
            for item in dict.fromkeys(preferences):
                st.write(item)
