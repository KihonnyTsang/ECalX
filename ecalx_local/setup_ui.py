"""First-run setup and local import; existing visual components are reused."""
import json
from io import BytesIO
import pandas as pd
import streamlit as st
from .ui import section_header
from . import SCIENCE_ROOT
from .configuration import save_config,read_config,config_dir,credential_configured,SCOPE,DEFAULT_LIMITS,LIMITS
from .import_data import FIELDS,read_table,preview,commit,default_mapping,EXPERIMENTAL_RESPONSES
from ecalx.data.repository import EvidenceRepository
from .field_protocol import imported_protocol
from .protocol_ui import reference_controls, build_reference, show_protocol
from .manual_ui import record_preview

def setup_page():
    section_header('设置','API 配置','填写在线研究所需的 API Key。本地研究无需配置。')
    st.caption('保存配置不会联网或产生费用。')
    try:cfg=read_config()
    except Exception:cfg={'model':None,'usage_limits':DEFAULT_LIMITS}
    with st.form('byo_settings',clear_on_submit=True):
        key=st.text_input('API Key',type='password')
        model=st.text_input('模型名称',value='')
        st.caption('首次配置请填写模型 ID，格式为 deepseek-ai/DeepSeek-型号。留空保留已保存的模型和密钥。')
        st.caption('在线研究可能产生费用，以供应商账单为准。每次研究和追问前会确认用量。')
        labels={'daily_requests':'每日最多模型请求次数','daily_tokens':'每日最多 Token 数量',
            'task_requests':'每任务最多模型请求次数','task_tokens':'每任务最多 Token 数量',
            'task_tool_calls':'每任务最多工具调用次数','output_tokens':'每请求最多输出 Token 数量',
            'request_seconds':'每请求时间上限（秒）','task_seconds':'每任务时间上限（秒）'}
        limits={}
        with st.expander('高级设置 · 用量上限',expanded=False):
            st.caption('可调低或在允许范围内调整；每日额度按 UTC 日期累计，研究和在线追问共用。未知供应商用量会阻止后续发送，自动重试0次。')
            for name,label in labels.items():
                _,low,high=LIMITS[name]
                limits[name]=st.number_input(label,min_value=low,max_value=high,value=cfg['usage_limits'][name],step=1)
        submitted=st.form_submit_button('仅保存本机配置',type='primary')
    if submitted:
        try:
            save_config({'model':model,'usage_limits':limits},key)
            cfg=read_config()
            st.success('已保存到受保护的本地配置；未发送任何 API 请求。')
        except ValueError as error:
            st.error('配置未保存：'+str(error))
        except Exception:
            st.error('配置未保存：请检查模型名称、API Key、用量上限及本机目录权限。')
    if cfg['model']:
        st.caption('当前已配置模型：'+cfg['model'])
    else:
        st.caption('尚未配置模型。')
    st.write('在线状态：'+('已配置，未经在线验证' if credential_configured() else '尚未配置有效 Key；离线可用'))
    with st.expander('帮助说明 · 配置位置与数据发送'):
        st.write(SCOPE)
        st.caption('配置位置：'+str(config_dir()))
        st.caption('材料工作区：'+str(SCIENCE_ROOT))
        st.caption('可用 ECALX_WORKSPACE 指定工作区。配置目录权限 700，文件权限 600。')
        st.code('python3 -m venv .venv\nsource .venv/bin/activate\npython -m pip install ".[dev]"\necalx',language='bash')


def data_page():
    section_header('设置','数据库构建','导入或录入材料数据，保存在本机。')
    st.caption('支持 CSV / Excel（.xlsx）、手动录入和论文 PDF 自动提取。数据经核对后保存在本机。')
    with st.expander('帮助说明 · 数据录入规则'):
        st.write('缺失字段保持空缺；电场起点、终点、变化幅度、峰值和上限分别记录，不互相代填。直接法与间接法分别保留。')
        st.write('数据来源尚未独立核验。导入数据后仍不提供正式性能预测或排名。')
    bulk, manual, papers = st.tabs(['批量导入现有数据','手动录入实验数据','论文数据自动提取'])
    with bulk:
        bulk_import()
    with manual:
        from .manual_ui import manual_entry
        manual_entry(SCIENCE_ROOT)
    with papers:
        from .paper_ui import paper_import
        paper_import(SCIENCE_ROOT)
    repo=EvidenceRepository(SCIENCE_ROOT/'data/evidence.sqlite3')
    records=repo.records()
    st.write('本机记录数：'+str(len(records)))
    if records:
        with st.expander('检索本机材料记录',expanded=True):
            query=st.text_input('检索化学式、来源或样品编号',key='data_search')
            response=st.selectbox('实验响应类型',[None,*EXPERIMENTAL_RESPONSES],key='data_response',
                                  format_func=lambda k:'全部响应' if k is None else ' · '.join(EXPERIMENTAL_RESPONSES[k]))
            minimum=maximum=None
            if response:
                a,b=st.columns(2)
                minimum=a.text_input('响应幅值下限（'+EXPERIMENTAL_RESPONSES[response][1]+'）',key='data_response_min').strip() or None
                maximum=b.text_input('响应幅值上限（'+EXPERIMENTAL_RESPONSES[response][1]+'）',key='data_response_max').strip() or None
            excluded=st.text_input('检索时排除元素',key='data_exclude',placeholder='例如 Pb')
            import re
            exclude_elements=[e for e in re.split(r'[,，、\s]+',excluded.strip()) if e]
            if hasattr(repo,'query'):
                try:
                    matched=repo.query(response=response,text=query,exclude_elements=exclude_elements,
                                       value_min=minimum,value_max=maximum)
                except ValueError as error:
                    st.error(str(error));matched=[]
            else:
                from ecalx.materials.formulas import formula_elements
                matched=[r for r in records if (response is None or getattr(r,response,None) is not None)
                         and query.casefold() in json.dumps(r.to_dict(),ensure_ascii=False).casefold()
                         and (not exclude_elements or (formula_elements(r.formula_normalized or r.formula_raw) is not None
                              and not set(exclude_elements)&formula_elements(r.formula_normalized or r.formula_raw)))]
            matches=[r.to_dict() for r in matched]
            st.caption(f'匹配 {len(matches)} 条；展示前 100 条。完整记录保存在本机数据库。')
            st.dataframe([record_preview(r) for r in matches[:100]],hide_index=True)
            if matches:
                selected_detail=st.selectbox('查看记录数值及原始出处',range(len(matches)),
                    format_func=lambda i:matches[i]['record_id'],key='data_record_detail')
                st.dataframe([record_preview(matches[selected_detail])],hide_index=True)
                with st.expander('完整记录与原 Excel 出处'):
                    st.json(matches[selected_detail])
        with st.expander('高级分析 · 加电方式与模型支持条件'):
            st.caption('这里单独选择研究参考；不改变原始文件、导入记录或历史任务。direct / indirect 可以同条件对照，分别保留，不默认等价或合并标签。')
            selected=st.selectbox('选择原始记录',range(len(records)),
                format_func=lambda i: records[i].record_id+' · '+records[i].formula_normalized+' · '+records[i].measurement_method)
            record=records[selected]
            original=imported_protocol(record)
            st.write(f"协议未知 · UNKNOWN（上传字段未核验）；真实端点 {original['start_kV_cm']} → {original['end_kV_cm']} kV/cm；"
                     f"提供的峰值 {original['peak_kV_cm']}；ΔE {original['delta_kV_cm']}；上限 {original['upper_bound_kV_cm']}。None 表示 null。")
            values=reference_controls('data_ref_'+record.record_id)
            model=st.selectbox('检查现有研究模型的适用性',['RandomForest','XGBoost'])
            if getattr(record,'experimental_response',False):
                st.caption('器件温跃和体积熵变仅作为实验资料，不进入现有本征响应预测或性能排名。')
            if st.button('检查参考协议与预测适用性',disabled=getattr(record,'experimental_response',False)):
                try:
                    from .exploratory import estimate, reason_text
                    protocol=build_reference(values)
                    show_protocol(protocol)
                    result=estimate(formula=record.formula_normalized,temperature_K=record.temperature_K,
                        delta_E_kV_cm=original['delta_kV_cm'],method=record.measurement_method,
                        sample_form=record.sample_form,model=model,e_start=record.e_start_kV_cm,
                        e_end=record.e_end_kV_cm,field_path=record.field_path,protocol=protocol)
                    st.write('当前条件暂不提供性能预测。')
                    for reason in result['reasons']:
                        st.write('未提供用户模型。' if reason=='no_user_model_provided' else reason_text(reason))
                    st.caption('参考协议未并入证据匹配，未改写真实条件。')
                except ValueError as error:
                    st.error(str(error))
    with st.expander('数据版本与导入记录'):
        st.json(repo.logs())


def bulk_import():
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
                        suggested=default_mapping(frame.columns).get(field)
                        default=options.index(suggested) if suggested else 0
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
                st.dataframe([record_preview(r) for r in result['preview']],hide_index=True)
                with st.expander('高级详情 · 完整导入记录'):
                    st.json(result['preview'])
                st.caption('来源尚未独立核验，缺少的电场起点和终点保持空缺。')
                if result['warnings']:st.dataframe(result['warnings'],hide_index=True)
                if result['errors']:
                    st.error('修正错误后重新上传；本次未写入数据。')
                    st.dataframe(result['errors'],hide_index=True)
                    st.download_button('下载错误报告',json.dumps(result['errors'],ensure_ascii=False,indent=2),'import-errors.json')
                if st.button('确认导入本机工作区',disabled=bool(result['errors']) or not result['records'],type='primary'):
                    log=commit(content,result)
                    st.success('已导入 '+str(log['inserted'])+' 条记录。')
                    st.session_state.pop('import_preview',None)
        except Exception as e:
            st.error('导入未完成：'+str(e))
