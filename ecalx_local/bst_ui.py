"""Independent research interpolation page; never modifies goals or evidence."""
import altair as alt
import pandas as pd
import streamlit as st

from .bst_tool import BSTUnavailable, load_interpolator
from .ui import section_header


KINDS = {
    'reference_digitized': '文献原始读图值',
    'temperature_interpolation': '温度插值',
    'composition_interpolation': '组成插值',
}


def result_heading(point):
    label = 'ΔT文献原始读图值' if point['result_kind'] == 'reference_digitized' else 'ΔT插值估计'
    return f"{label}：约{point['delta_T_magnitude_K']:.2f} K"


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
                            range=['#117c78', '#3973ac', '#c87530', '#a53b39']),
                        legend=alt.Legend(orient='bottom', columns=2)),
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
    ).properties(height=330).configure_view(stroke=None)


def bst_page():
    section_header('独立研究工具', 'BST电卡插值', '基于文献读图数据的研究性插值。')
    st.caption('仅本工具适用：Ba₁₋ₓSrₓTiO₃块体陶瓷，x为0.05–0.35；标称撤场幅值为10、20、30 kV/cm。')
    reference = st.file_uploader('选择参考CSV', type=['csv'], key='reference_csv_upload',
                                 help='请使用合法取得的204点脱敏参考CSV；文件只在本机计算，不写入材料数据库。')
    st.caption('参考数据未随软件分发，需自行合法取得。')
    columns = st.columns(3)
    x = columns[0].text_input('Sr组成比例 x', value=None if 'bst_x' in st.session_state else '0.20', key='bst_x', help='摩尔分数，例如0.20表示20%。')
    temperature = columns[1].text_input('温度（K）', value=None if 'bst_temperature' in st.session_state else '323', key='bst_temperature')
    field = columns[2].text_input('标称电场（kV/cm）', value=None if 'bst_field' in st.session_state else '30', key='bst_field', help='文献标称撤场幅值，仅支持10、20、30。')
    if not all(v.strip() for v in (x, temperature, field)):
        st.info('请填写Sr组成比例、温度和标称电场。')
        return
    try:
        model = load_interpolator(reference.getvalue() if reference is not None else None)
    except BSTUnavailable as error:
        st.warning(str(error))
        return
    try:
        _, _, _, lo, hi = model.coverage(x, field)
        st.caption(f'当前组成与电场的温度覆盖：{lo:g}–{hi:g} K。')
        point = model.predict(x, temperature, field)
        curve = model.curve(x, field, include_temperature=temperature)
        references = model.reference_rows(x, field)
    except ValueError as error:
        st.warning('不支持此输入：'+str(error))
        return

    st.subheader(result_heading(point))
    st.write('结果类型：'+KINDS[point['result_kind']])
    st.caption('ΔT表示正值降温幅值；标称电场不代表已核实的完整加电波形。')
    st.altair_chart(curve_chart(point, curve, references), use_container_width=True)
    st.caption('圆点：文献原始读图值；虚线：参考组成的温度插值连线；实线：组成插值；菱形：当前输入。')

    st.markdown('**参考组成与来源**')
    st.table(pd.DataFrame([{
        '参考组成 x': f"{ref['sr_molar_fraction']:.2f}",
        '组成权重': f"{ref['composition_weight']:.1%}",
        '当前温度取值方式': KINDS[ref['evaluation_kind']],
        'ΔT（K）': f"{ref['at_query_T_value_K']:.2f}",
        '图号': '图'+ref['figure'],
    } for ref in point['reference_sources']]))
    st.markdown('来源论文：Dai等，*Direct and indirect measurement of large electrocaloric effect '
                'in barium strontium titanate ceramics* · [DOI：10.1111/ijac.13384](https://doi.org/10.1111/ijac.13384) · 图8A–D。')
    with st.expander('查看支撑读图点'):
        st.table(pd.DataFrame([{
            '参考组成 x': f"{ref['sr_molar_fraction']:.2f}",
            '原始温度（K）': f"{row['temperature_K']:.1f}",
            '文献原始读图值（K）': f"{row['delta_T_magnitude_K']:.2f}",
            '图号': '图'+row['figure'],
            '记录编号': row['observation_id'],
        } for ref in point['reference_sources'] for row in ref['source_records']]))
