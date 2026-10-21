"""Plain-language scopes for source-reported amplitude evidence."""


def amplitude_metrics(summary):
    if 'delta_E_matches' not in summary:
        return
    import streamlit as st
    context=summary.get('delta_E_context_matches')
    columns = st.columns(2)
    columns[0].metric('符合 ΔE 的记录', '未指定' if summary.get('delta_E_matches') is None else summary['delta_E_matches'])
    columns[1].metric('同时符合其他条件', '未指定' if context is None else context)
    with st.expander('帮助说明 · 匹配数量怎么算'):
        st.write('匹配数量首先按 ΔE 统计；如果还设置了温度、组分等条件，系统会一并筛选。只有实验条件完整一致的数据，才会计入严格匹配。只有 ΔE、但缺少完整路径的数据，仍会保留并参与普通检索。')
        st.caption('文献记录数不等于独立实验次数，也不能证明候选性能。')
        st.write(f"已核实加电路径：{summary.get('verified_path_records', 0)} 条；加电路径未确认：{summary.get('path_unknown_records', 0)} 条。")
        st.caption('这两项按全库统计，与 ΔE 匹配数量可能重叠，不能相加。')
        st.write(f"符合 ΔE 等条件、但加电路径尚未确认：{summary['amplitude_only_auxiliary_matches']} 条。")
        st.caption('0↔E 是常见的参考加电方式；仅有 ΔE 时，不会据此补填文献的起点、终点或测量分支。')


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
