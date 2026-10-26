"""Literature interpolation inside the research workbench; goals stay free."""
import altair as alt
import pandas as pd
import streamlit as st

KINDS = {
    'reference_digitized': '文献原始读图值',
    'temperature_interpolation': '温度插值',
    'composition_interpolation': '组成插值',
}


def result_heading(point):
    label = 'ΔT 文献读图值' if point['result_kind'] == 'reference_digitized' else 'ΔT 插值估计'
    return f"{label}：约 {point['delta_T_magnitude_K']:.2f} K"


def curve_chart(point, curve, references):
    """Circles are source readings; dashed lines show T interpolation only."""
    rows = []
    lo, hi = point['temperature_coverage_K']
    for ref in point['reference_sources']:
        x = ref['sr_molar_fraction']
        # Evaluate reference lines at the union of knots, including the strict
        # intersection endpoints. Do not clip away a supporting source knot.
        for value in curve:
            source = next(s for s in value['reference_sources'] if s['sr_molar_fraction'] == x)
            rows.append({'温度': value['temperature_K'], 'ΔT': source['at_query_T_value_K'],
                         '系列': f'参考 x={x:g} · 图{ref["figure"]}', '类型': '温度插值', '组成': x})
    for ref in references:
        if lo <= ref['temperature_K'] <= hi:
            rows.append({'温度': ref['temperature_K'], 'ΔT': ref['delta_T_magnitude_K'],
                         '系列': f'参考 x={ref["sr_molar_fraction"]:g} · 图{ref["figure"]}',
                         '类型': '文献原始读图值', '组成': ref['sr_molar_fraction']})
    if point['result_kind'] == 'composition_interpolation':
        for value in curve:
            rows.append({'温度': value['temperature_K'], 'ΔT': value['delta_T_magnitude_K'],
                         '系列': f'组成插值 x={point["sr_molar_fraction"]:g}',
                         '类型': '组成插值', '组成': point['sr_molar_fraction']})
    rows.append({'温度': point['temperature_K'], 'ΔT': point['delta_T_magnitude_K'],
                 '系列': '当前输入', '类型': KINDS[point['result_kind']], '组成': point['sr_molar_fraction']})
    for row in rows:
        # Vega's default accessibility text otherwise repeats unrounded y values.
        row['说明'] = f"{row['系列']}；{row['类型']}；温度{row['温度']:.1f} K；ΔT约{row['ΔT']:.2f} K"
    base = alt.Chart(pd.DataFrame(rows)).encode(
        x=alt.X('温度:Q', title='温度（K）', scale=alt.Scale(domain=[lo, hi], nice=False)),
        y=alt.Y('ΔT:Q', title='ΔT降温幅值（K）', scale=alt.Scale(zero=False)),
        color=alt.Color('系列:N', title=None, scale=alt.Scale(
                            domain=list(dict.fromkeys(row['系列'] for row in rows)),
                            range=['#9aaab5', '#547b83', '#117c78', '#193248']),
                        legend=alt.Legend(orient='bottom', columns=2, labelLimit=260)),
        tooltip=[alt.Tooltip('类型:N'), alt.Tooltip('组成:Q', title='Sr比例 x'),
                 alt.Tooltip('温度:Q', title='温度（K）', format='.1f'),
                 alt.Tooltip('ΔT:Q', title='ΔT（K）', format='.2f')],
        description=alt.Description('说明:N'),
    )
    return alt.layer(
        base.transform_filter((alt.datum['类型'] == '温度插值') & (alt.datum['系列'] != '当前输入')).mark_line(strokeDash=[5, 4]),
        base.transform_filter((alt.datum['类型'] == '组成插值') & (alt.datum['系列'] != '当前输入')).mark_line(strokeWidth=2.5),
        base.transform_filter((alt.datum['类型'] == '文献原始读图值') & (alt.datum['系列'] != '当前输入')).mark_point(filled=True, size=40),
        base.transform_filter(alt.datum['系列'] == '当前输入').mark_point(shape='diamond', filled=True, size=120),
    ).properties(height=285).configure_view(stroke=None).configure_axis(gridColor='#edf1f3', labelColor='#687985', titleColor='#425966', titleFontWeight=500)


def render_result(result):
    point,curve,references=result['point'],result['curve'],result['references']
    with st.container(key='bst_result'):
        st.subheader(result_heading(point))
        st.caption('文献撤场正温降幅值 · 研究性参考，非本次协议实测值。')
        st.caption(f"Ba₁₋ₓSrₓTiO₃ · x = {point['sr_molar_fraction']:g} · {point['temperature_K']:g} K · ΔE {point['nominal_delta_E_kV_cm']:g} kV/cm" if 'nominal_delta_E_kV_cm' in point else
                   f"Ba₁₋ₓSrₓTiO₃ · x = {point['sr_molar_fraction']:g} · {point['temperature_K']:g} K")
        chart = st.expander('查看参考曲线', on_change='rerun', key='bst_reference_chart')
        if chart.open:
            with chart:
                st.altair_chart(curve_chart(point,curve,references),use_container_width=True)
        refs='、'.join(f"x = {ref['sr_molar_fraction']:g}（{ref['composition_weight']:.0%}）" for ref in point['reference_sources'])
        with st.expander('参考依据'):
            st.write('参考组成：'+refs)
            st.markdown('Dai 等 · [论文来源](https://doi.org/10.1111/ijac.13384) · 图 8A–D')
            lo,hi=point['temperature_coverage_K']
            st.write(f'BST 块体陶瓷，x = 0.05–0.35；ΔE 支持 10、20、30 kV/cm。本次温度范围 {lo:g}–{hi:g} K，不外推。')
            st.caption('ΔT 为正值降温幅值。参考数据为文献曲线读图值，完整加电波形未独立核实。圆点为读图点，虚线为温度插值，实线为组成插值，菱形为当前条件。')
            st.table(pd.DataFrame([{
                '参考组成 x':f"{ref['sr_molar_fraction']:.2f}",
                '组成权重':f"{ref['composition_weight']:.1%}",
                '取值方式':KINDS[ref['evaluation_kind']],
                'ΔT（K）':f"{ref['at_query_T_value_K']:.2f}",'图号':'图'+ref['figure'],
            } for ref in point['reference_sources']]))
            st.caption('支撑读图点')
            st.dataframe(pd.DataFrame([{
                '参考组成 x':f"{ref['sr_molar_fraction']:.2f}",
                '温度（K）':f"{row['temperature_K']:.1f}",
                '读图 ΔT（K）':f"{row['delta_T_magnitude_K']:.2f}",
                '图号':'图'+row['figure'],'记录编号':row['observation_id'],
            } for ref in point['reference_sources'] for row in ref['source_records']]),hide_index=True)


def open_database():
    st.session_state.workspace='设置'
    st.session_state.main_navigation=None
    st.session_state.settings_page='数据库构建'


def research_tool(prepared, *, agent=None, task=None, show_cached=True):
    """Present the unchanged eligibility and calculation results in place."""
    from .bst_research import eligibility,calculate,signature
    results=task.get('literature_results',[]) if task else []
    current=next((r for r in results if r['input_sha256']==signature(prepared)),None)
    if current:
        render_result(current)
        st.caption('已随研究历史保存')
        return
    status=eligibility(prepared)
    if status['status']!='available':
        if prepared.get('raw',{}).get('composition'):
            with st.expander('插值详情'):
                st.write(status['notice'])
                if status['status']=='missing_data':
                    st.button('设置 → 数据库构建',key='open_reference_settings',on_click=open_database)
        return
    key=signature(prepared)
    cached=st.session_state.get('literature_draft')
    if cached and cached['input_sha256']==key and not task:
        prepared['literature_results']=[cached]
        if show_cached:
            render_result(cached)
        return
    with st.container(border=True):
        a,b=st.columns([2,1],vertical_alignment='center')
        a.markdown('**BST 电卡插值**')
        lo,hi=status['temperature_coverage_K']
        a.caption(f"x = {float(status['x']):g} · {status['temperature_K']:g} K · ΔE {status['field_kV_cm']:g} kV/cm")
        if b.button('计算电卡温变',type='primary',use_container_width=True,
                    key='calculate_literature_'+(task['id'] if task else 'draft')):
            try:
                result=calculate(prepared)
                if task:
                    agent.save_literature_result(task['id'],result)
                else:
                    st.session_state.literature_draft=result
                st.rerun()
            except (ValueError,OSError):
                st.error('参考数据或条件已变化，请检查后重试。')
        with st.expander('参考依据'):
            st.markdown('[Dai 等 · 文献来源](https://doi.org/10.1111/ijac.13384) · 图 8A–D')
            st.caption(f'BST 块体陶瓷，x = 0.05–0.35；ΔE 支持 10、20、30 kV/cm。本次温度范围 {lo:g}–{hi:g} K。')
