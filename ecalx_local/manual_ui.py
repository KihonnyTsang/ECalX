"""One-record entry, preview and explicit commit through the bulk pipeline."""
import pandas as pd
import streamlit as st
from . import SCIENCE_ROOT
from .import_data import manual_content, preview_manual, commit
from ecalx.data.repository import EvidenceRepository


def record_preview(record):
    """Show experimental quantities first; keep the complete stored record available."""
    raw = record.get('raw_metadata_json') or {}
    normalized = raw.get('normalized_fields') or {}
    return {'化学式':record['formula_raw'], '温度 K':record.get('temperature_K'),
            'ΔE kV/cm':record.get('e_delta_kV_cm', normalized.get('e_delta_kV_cm')),
            'ΔT K':record.get('delta_T_ad_K'), 'ΔS J/(kg K)':record.get('delta_S_J_kgK'),
            '器件温跃 |ΔTj| K':record.get('delta_T_jump_abs_K'),
            '体积熵变 |ΔS| kJ/(K·m³)':record.get('delta_S_vol_abs_kJ_K_m3'),
            '样品编号':record.get('specimen_id'), '分支':record.get('field_path'),
            '原报告场 kV/cm':record.get('e_reported_kV_cm'),
            '报告场含义':record.get('field_level_role') or record.get('e_reported_kind'),
            '数值派生方式':record.get('value_derivation'), '归一化口径':record.get('normalization_basis'),
            '原Excel文件':record.get('source_workbook'), '原工作表':record.get('source_sheet'),
            '温度单元格':record.get('temperature_cell'), '响应单元格':record.get('response_cell'),
            '论文许可':raw.get('paper_license'),
            '方法':{'direct':'直接法','indirect':'间接法'}.get(record.get('measurement_method'),'未提供'),
            '电场起点 kV/cm':record.get('e_start_kV_cm'), '电场终点 kV/cm':record.get('e_end_kV_cm'),
            'ΔT 原始定义':{'signed_delta_t':'带符号温变','positive_drop_magnitude':'正温降幅值','absolute_delta_t':'绝对温变幅值'}.get(record.get('delta_t_basis'),'按来源记录'), '来源类型':raw.get('source_type'),
            '实验记录编号':raw.get('source_record_id'), 'DOI':record.get('doi'),
            'URL':record.get('url'), '来源核验':'待核验',
            '录入时间 UTC':raw.get('entered_at_utc'),
            '论文文件':raw.get('paper',{}).get('filename'),
            '论文页码':raw.get('source_location',{}).get('page'),
            '论文位置':raw.get('source_location',{}).get('label')}


def manual_entry(workspace=None):
    # A single form submits all quantities together, avoiding reruns between
    # individual edits. Confirmation still checks the current submitted bytes
    # against the preview, so modified inputs cannot commit a stale preview.
    with st.form('manual_record'):
        _manual_entry(workspace)


def _manual_entry(workspace=None):
    root = workspace or SCIENCE_ROOT
    st.caption('填写实验数值与来源，预览核对后保存。')
    values = {}
    def text(key, label, **kwargs):
        values[key] = st.text_input(label, key='manual_'+key, **kwargs)
    def choice(key, label, options, labels=None):
        values[key] = st.selectbox(label, options, key='manual_'+key,
                                   format_func=lambda v: labels.get(v,v) if labels else v)
    text('formula', '材料化学式（必填）', placeholder='例如 BaTiO3')
    a,b = st.columns([2,1])
    with a:
        text('temperature', '实验测试温度（可选）')
    with b:
        choice('temperature_unit', '测试温度单位', ['K','°C','°F'])
    a,b = st.columns([2,1])
    with a:
        text('e_delta', '电场变化幅度 ΔE（可选）')
    with b:
        choice('field_unit', '录入电场单位', ['kV/cm','kV/mm','MV/m','V/m','V/cm','V/mm'])
    a,b = st.columns([2,1])
    with a:
        text('delta_t', '电卡温变 ΔT / 正温降幅值（可选）')
    with b:
        choice('delta_t_unit', '录入 ΔT 单位', ['K','°C'])
    choice('delta_t_basis', 'ΔT 原始定义', ['unspecified_user_quantity','signed_delta_t','positive_drop_magnitude'],
           {'unspecified_user_quantity':'未知 / 按来源原样记录','signed_delta_t':'带符号 ΔT','positive_drop_magnitude':'正温降幅值（保留正值，不改为负 ΔT）'})
    a,b = st.columns([2,1])
    with a:
        text('delta_s', '电卡熵变 ΔS（可选）')
    with b:
        choice('delta_s_unit', '录入 ΔS 单位', ['J/(kg K)','mJ/(g K)','J/(g K)'])
    choice('method', '测量方法', ['unknown','direct','indirect'], {'unknown':'未知','direct':'直接法','indirect':'间接法'})
    choice('source_type', '来源类型', ['本人实验','文献','其他'])
    text('doi', '来源 DOI（可选）')
    text('url', '来源 URL（可选）')
    text('source_record_id', '本地实验记录编号（可选）')
    with st.expander('样品、实验协议与备注（可选）'):
        text('specimen_id', '样品编号')
        text('sample_form', '样品形态')
        text('protocol', '实验协议')
        values['notes'] = st.text_area('备注', key='manual_notes')
    with st.expander('高级电场与 ΔT 符号定义（可选）'):
        st.caption('仅填写来源实际提供的数值，单位使用上方电场单位。只有 ΔE 时请留空端点；峰值、上限不代替 ΔE。')
        for key,label in [('e_start','电场起点'),('e_end','电场终点'),
                          ('e_peak','实际峰值'),('e_max','电场上限')]:
            text(key,label)
        choice('field_path', '加载 / 撤场分支', ['unknown','loading','unloading'],
               {'unknown':'未知','loading':'加载','unloading':'撤场'})
        text('sign_convention', '来源的 ΔT 符号说明', placeholder='可留空；例如 T_after − T_before')
    st.caption('未提供的字段留空。')
    content = manual_content(values)
    if st.form_submit_button('预览并校验这条记录', type='primary'):
        _, prepared = preview_manual(values)
        st.session_state.manual_preview = prepared
    prepared = st.session_state.get('manual_preview')
    if prepared and prepared['sha256'] != __import__('hashlib').sha256(content).hexdigest():
        st.info('内容已修改，请重新预览并校验。')
        prepared = None
    if prepared:
        st.subheader('保存前预览')
        if prepared['preview']:
            st.dataframe([record_preview(r) for r in prepared['preview']],hide_index=True)
            with st.expander('完整记录与原始定义'):
                st.json(prepared['preview'][0],expanded=False)
        for error in prepared['errors']:
            st.error('校验未通过：'+error['message'])
        for warning in prepared['warnings']:
            st.warning(warning['message'])
        if not prepared['errors']:
            st.success('校验通过，请核对后保存。')
            st.caption('只有点击“确认保存这条记录”才写入数据库。')
        if st.form_submit_button('确认保存这条记录', disabled=bool(prepared['errors']) or not prepared['records']):
            try:
                log = commit(content, prepared, root)
                st.session_state.manual_saved = {'log':log, 'ids':[r.record_id for r in prepared['records']]}
            except (ValueError, OSError) as error:
                st.error('保存未完成：'+str(error))
    saved = st.session_state.get('manual_saved')
    if saved:
        repo = EvidenceRepository(root/'data/evidence.sqlite3')
        records = repo.records()
        st.success(('此记录已保存，重复提交未新增。' if saved['log'].get('already_imported') else '已新增 1 条记录。')+
                   f' 本机记录数：{len(records)}')
        saved_records = [r.to_dict() for r in records if r.record_id in saved['ids']]
        st.dataframe([record_preview(r) for r in saved_records],hide_index=True)
        with st.expander('已保存的完整记录'):
            st.json(saved_records,expanded=False)
