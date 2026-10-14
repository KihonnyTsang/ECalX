"""First-run setup and local import; existing visual components are reused."""
import json
from io import BytesIO
import pandas as pd
import streamlit as st
from .ui import section_header
from . import SCIENCE_ROOT
from .configuration import save_config,read_config,config_dir,credential_configured,SCOPE
from .import_data import FIELDS,read_table,preview,commit
from ecalx.data.repository import EvidenceRepository
from ecalx.agent.config import MODEL
from .field_protocol import imported_protocol
from .protocol_ui import reference_controls, build_reference, show_protocol

def setup_page():
    section_header('BYO API & DATA','首次配置','使用自己的 API 与材料数据；离线科研无需 API Key。')
    st.markdown('1. 从侧栏 **我的材料数据** 上传 CSV 或 Excel，映射字段并核对预览。\n2. 新建研究，输入可选条件并确认，再执行本地科研工具。\n3. 如需 DeepSeek 在线 Agent，填写下方配置，并在每次研究前确认发送范围和预算。')
    st.code('python3 -m venv .venv\nsource .venv/bin/activate\npython -m pip install ".[dev]"\necalx',language='bash')
    st.caption('本机工作区：'+str(SCIENCE_ROOT)+'；可用 ECALX_WORKSPACE 指定。配置保存在 '+str(config_dir())+'（目录700、文件600）。')
    st.info('配置不会检测 API，也不会扣费。真实在线验收须由你另行执行。本地示例明确标为 SYNTHETIC_TEST_DATA。')
    try:cfg=read_config()
    except Exception:cfg={'model':MODEL,'prices':{},'daily_cny':None}
    with st.form('byo_settings',clear_on_submit=True):
        key=st.text_input('SiliconFlow API Key（留空保留已有 Key）',type='password')
        model=st.text_input('SiliconFlow DeepSeek 模型 ID',value=cfg['model'])
        st.caption('请按供应商当前价格填写。模型必须支持 Function Calling；这里只检查配置，联网时才会验证响应契约。')
        a,b=st.columns(2)
        inp=a.text_input('输入单价（元/百万 Token）',value=str(cfg['prices'].get('input_per_million','')))
        out=b.text_input('输出单价（元/百万 Token）',value=str(cfg['prices'].get('output_per_million','')))
        cap=st.text_input('每日本地估算预算（元）',value=str(cfg['daily_cny'] or ''))
        submitted=st.form_submit_button('仅保存本机配置',type='primary')
    if submitted:
        try:
            save_config({'model':model,'prices':{'input_per_million':float(inp),'output_per_million':float(out)},'daily_cny':float(cap)},key)
            st.success('已保存到受保护的本地配置；未发送任何 API 请求。')
        except Exception:
            st.error('配置未保存：请检查模型ID、Key格式、正数单价和每日预算，以及配置目录700权限。')
    st.write('在线状态：'+('已配置，未经在线验证' if credential_configured() else '尚未配置有效 Key；离线可用'))
    st.caption(SCOPE)
    st.caption('公开模式未装载模型；ΔT/ΔS预测 unknown；M5正式推荐禁用。')


def data_page():
    section_header('BYO DATA','我的材料数据','原始文件保留在你的本机工作区；不因导入数据启用性能预测。')
    st.caption('留空字段保持 null。起点、终点、ΔE与场上限分别映射；direct / indirect 与实验协议分别保留。')
    st.caption('e_max 是上限，e_peak 是实际施加峰值，e_delta 是变化幅度；三者不互相代填。上传数值与协议说明均未经独立核验。')
    uploaded=st.file_uploader('上传 UTF-8 CSV 或 Excel .xlsx',type=['csv','xlsx'])
    if uploaded:
        content=uploaded.getvalue();sheet=0
        try:
            if uploaded.name.lower().endswith('.xlsx'):
                names=pd.ExcelFile(BytesIO(content),engine='openpyxl').sheet_names
                sheet=st.selectbox('Excel工作表',names)
            frame=read_table(content,uploaded.name,sheet)
            st.dataframe(frame.head(15),hide_index=True)
            with st.form('data_mapping'):
                mapping={}
                options=['未提供']+list(frame.columns)
                with st.expander('字段映射（至少选择材料化学式）',expanded=True):
                    a,b=st.columns(2)
                    for i,(field,label) in enumerate(FIELDS.items()):
                        default=options.index(field) if field in options else 0
                        chosen=(a if i%2==0 else b).selectbox(label,options,index=default,key='map_'+uploaded.name+'_'+field)
                        if chosen!='未提供':mapping[field]=chosen
                with st.expander('统一单位（仅用于没有单位列/单元格的数值）'):
                    units={}
                    for field,label,options in [('temperature_unit','温度单位',['未提供','K','°C','°F']),
                         ('field_unit','电场单位',['未提供','kV/cm','kV/mm','MV/m','V/m','V/cm','V/mm']),
                         ('delta_t_unit','ΔT单位',['未提供','K','°C']),
                         ('delta_s_unit','ΔS单位',['未提供','J/(kg K)','mJ/(g K)','J/(g K)'])]:
                        v=st.selectbox(label,options,key='units_'+field)
                        if v!='未提供':units[field]=v
                check=st.form_submit_button('预览并验证字段',type='primary')
            if check:
                st.session_state.import_preview=preview(content,uploaded.name,mapping,units=units,sheet=sheet)
            result=st.session_state.get('import_preview')
            if result and result['sha256']==__import__('hashlib').sha256(content).hexdigest():
                st.write(f"文件 {result['rows']} 行；有效 {len(result['records'])} 行；错误 {len(result['errors'])} 行。")
                st.dataframe(pd.DataFrame(result['preview']).drop(columns=['raw_metadata_json'],errors='ignore'),hide_index=True)
                st.write('原数据协议状态：UNKNOWN / 未独立核验。缺失的真实端点保持 null；参考协议须在下方单独启用。')
                if result['warnings']:st.dataframe(result['warnings'],hide_index=True)
                if result['errors']:
                    st.error('修正错误后重新上传；本次未写入数据。')
                    st.dataframe(result['errors'],hide_index=True)
                    st.download_button('下载错误报告',json.dumps(result['errors'],ensure_ascii=False,indent=2),'import-errors.json')
                if st.button('确认导入本机工作区',disabled=bool(result['errors']) or not result['records'],type='primary'):
                    log=commit(content,result)
                    st.success('已导入 '+str(log['inserted'])+' 条；数据版本 '+log['data_version'])
                    st.session_state.pop('import_preview',None)
        except Exception as e:
            st.error('导入未完成：'+str(e))
    repo=EvidenceRepository(SCIENCE_ROOT/'data/evidence.sqlite3')
    records=repo.records()
    st.write('本机记录数：'+str(len(records)))
    if records:
        st.subheader('参考协议与探索性分析')
        st.caption('这里单独选择研究参考；不改变原始文件、导入记录或历史任务。direct / indirect 可以同条件对照，分别保留，不默认等价或合并标签。')
        selected=st.selectbox('选择原始记录',range(len(records)),
            format_func=lambda i: records[i].record_id+' · '+records[i].formula_normalized+' · '+records[i].measurement_method)
        record=records[selected]
        original=imported_protocol(record)
        st.write(f"协议未知 · UNKNOWN（上传字段未核验）；真实端点 {original['start_kV_cm']} → {original['end_kV_cm']} kV/cm；"
                 f"提供的峰值 {original['peak_kV_cm']}；ΔE {original['delta_kV_cm']}；上限 {original['upper_bound_kV_cm']}。None 表示 null。")
        values=reference_controls('data_ref_'+record.record_id)
        model=st.selectbox('检查现有研究模型的适用性',['RandomForest','XGBoost'])
        if st.button('检查参考协议与预测适用性'):
            try:
                from .exploratory import estimate, reason_text
                protocol=build_reference(values)
                show_protocol(protocol)
                result=estimate(formula=record.formula_normalized,temperature_K=record.temperature_K,
                    delta_E_kV_cm=record.e_delta_kV_cm,method=record.measurement_method,
                    sample_form=record.sample_form,model=model,e_start=record.e_start_kV_cm,
                    e_end=record.e_end_kV_cm,field_path=record.field_path,protocol=protocol)
                st.write('预测适用性：'+result['status']+'；ΔT：unknown')
                for reason in result['reasons']:
                    st.write('未提供用户模型。' if reason=='no_user_model_provided' else reason_text(reason))
                st.caption('参考协议未并入证据匹配，未改写真实条件。')
            except ValueError as error:
                st.error(str(error))
    with st.expander('数据版本与导入记录'):
        st.json(repo.logs())
