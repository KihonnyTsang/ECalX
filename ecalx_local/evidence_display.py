"""Plain-language scopes for source-reported amplitude evidence."""


def amplitude_metrics(summary):
    if 'delta_E_matches' not in summary:
        return
    import streamlit as st
    columns = st.columns(3)
    for col, label, key in zip(columns,
        ('ΔE幅度匹配记录','完整路径已核实记录','路径未知记录'),
        ('delta_E_matches','verified_path_records','path_unknown_records')):
        value=summary.get(key)
        col.metric(label,'未指定' if value is None else value)
    st.caption(summary['amplitude_count_scope'])
    st.caption('完整协议口径不等于检索准入：缺端点的 ΔE 原文数据仍保留标签，可用于幅度查询与分口径描述统计。')
    with st.expander('可选常见参考解释：0↔E'):
        st.write('零场与高场之间的变场是常见参考。这里只作 ASSUMED 协议解释，不写入真实端点、升降场分支或性能符号，也不限定本次 ΔE 模型估计。若明确要求该路径，严格路径匹配和模型拒答边界继续生效。')
    st.caption('路径核实与路径未知按全库计数；路径核实来自独立覆盖层。幅度匹配记录可能属于其中任一类，三项不相加。')
    context=summary.get('delta_E_context_matches')
    context='未指定' if context is None else str(context)+' 条'
    st.write(f"符合温度/组成等条件的幅度匹配：{context}；幅度匹配、路径未确认辅助证据：{summary['amplitude_only_auxiliary_matches']} 条。")


def statistics_lines(data):
    stats=data.get('descriptive_statistics',[])
    if not stats:
        return []
    lines=['', '## 原文报告 ΔT 的描述统计', '',
        '按来源、组成、温度、ΔE、测量方法、物理量口径、符号和已知协议分别统计；未知路径仍可利用原始标签。均值不是统一物理量的预测或同协议证明，记录数不是独立实验数。',
        f"统计分组 {data.get('descriptive_statistics_group_count',len(stats))} 组，本收据保存前 {len(stats)} 组。"]
    for s in stats:
        lines.append(f"- {s['formula']} · {s['method']} · {s['temperature_K']:g} K · ΔE={s['delta_E_kV_cm']:g} kV/cm；"
            f"口径 {s['delta_t_basis']} / 符号 {s['sign_convention']}；来源 {s['source']}；{s['record_count']} 条；"
            f"原文报告 ΔT 范围 {s['reported_delta_T_min_K']:g}–{s['reported_delta_T_max_K']:g} K，均值 {s['reported_delta_T_mean_K']:g} K；"
            + ('幅度辅助证据，不能证明指定路径性能。' if s['match_scope']=='amplitude_only_path_unconfirmed' else '原文条件匹配，不代表候选实测。'))
    return lines


def statistics_panel(data):
    stats=data.get('descriptive_statistics',[])
    if not stats:
        return
    import streamlit as st
    with st.expander('原文报告 ΔT 描述统计 · 方法与物理量分别保留'):
        st.caption(statistics_lines(data)[3])
        st.caption(statistics_lines(data)[4])
        st.dataframe([{'组成':s['formula'],'方法':s['method'],'温度 K':s['temperature_K'],
            'ΔE kV/cm':s['delta_E_kV_cm'],'物理量口径':s['delta_t_basis'],'符号':s['sign_convention'],
            '来源':s['source'],'记录数':s['record_count'],'原文标签均值 K':s['reported_delta_T_mean_K'],
            '匹配范围':'幅度辅助，路径未确认' if s['match_scope']=='amplitude_only_path_unconfirmed' else '条件匹配'} for s in stats],hide_index=True)
