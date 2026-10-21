"""Local upload -> extract -> edit -> validate -> explicit save workflow."""
from dataclasses import asdict
import hashlib
import json

import pandas as pd
import streamlit as st

from . import PERSONAL
from .import_data import FIELDS, commit
from .paper_extract import extract_pdf, prepare_review, retain_local_draft, CORE
from .manual_ui import record_preview
from .paper_batch import process_batch, summarize, import_automatic, revoke_paper_import, MAX_BATCH_PAPERS
from .paper_policy import auto_gate

EDIT_FIELDS = ('formula','composition','temperature','temperature_unit','e_delta','field_unit',
               'delta_t','delta_t_unit','delta_s','delta_s_unit','method','sample_form','specimen_id',
               'doi','delta_t_basis','sign_convention','e_start','e_end','e_peak','e_max','field_path','protocol','notes')
LABELS={**FIELDS,'selected':'选择保存','candidate_id':'候选编号','page':'页码','location':'来源位置',
        'status':'状态','missing':'缺失字段','warnings':'核对提示'}


def paper_import(workspace):
    st.subheader('论文数据自动提取')
    st.caption('本地离线解析正文、表格和图注。每篇最多 20 MB、100 页；每批最多 200 篇、合计 500 MB。')
    st.info('PDF 和证据只保存在本机，不调用付费 API。引用数据单独保存为线索，不写入当前论文实验结果。')
    if PERSONAL:
        st.warning('论文 MVP 请使用 BYO 开发工作区。此入口在个人模式禁用，避免改动原科研数据库。')
        return
    uploads=st.file_uploader('上传论文 PDF（可多选）',type=['pdf'],accept_multiple_files=True,key='paper_upload')
    if len(uploads)>MAX_BATCH_PAPERS or sum(getattr(u,'size',len(u.getvalue())) for u in uploads)>500_000_000:
        st.error('每批最多 200 篇、合计 500 MB，请分批上传。');return
    signature=tuple((u.name,hashlib.sha256(u.getvalue()).hexdigest()) for u in uploads)
    if st.button('提取论文数据',type='primary',disabled=not uploads,key='paper_extract'):
        with st.spinner('正在本机解析 PDF，未向外部发送内容…'):
            jobs=process_batch(((u.name,u.getvalue()) for u in uploads),workspace)
        st.session_state.paper_jobs=jobs
        st.session_state.paper_signature=signature
        st.session_state.paper_generation=st.session_state.get('paper_generation',0)+1
        st.session_state.pop('paper_prepared',None)
        st.session_state.pop('paper_saved',None)
        st.session_state.pop('paper_auto_logs',None)
    jobs=st.session_state.get('paper_jobs',[])
    if not jobs:return
    if signature != st.session_state.get('paper_signature'):
        st.warning('上传列表已变化，请重新提取；旧预览不能保存。');return
    generation=st.session_state.paper_generation
    valid=[job for job in jobs if 'draft' in job]
    for job in jobs:
        if 'error' in job:st.error(job['filename']+'：'+job['error'])
        if 'duplicate_pdf' in job:st.caption(job['filename']+'：相同 PDF，已跳过重复解析。')
    if not valid:return
    total=sum(len(j['draft'].candidates) for j in valid)
    todo=sum(r['status']=='需要图像数字化' for j in valid for r in j['draft'].candidates)
    summary=summarize(jobs,workspace)
    logs=st.session_state.get('paper_auto_logs',[])
    st.write(f"处理论文 {summary['papers_processed']} 篇 · 自动入库 {sum(l['inserted'] for l in logs if not l.get('already_imported'))} 条 · 待确认 {summary['needs_review']} 条 · 引用数据 {summary['cited_records']} 条 · 重复数据 {summary['duplicate_records']} 条 / PDF {summary['duplicate_papers']} 篇")
    st.caption(f"规则预检通过 {summary['auto_eligible']} 条；理论/模拟 {summary['theory_records']} 条（属于待确认队列）；数字化待办 {todo} 条。重复计数为重叠标记，不与各队列相加。")
    gate=auto_gate()
    if not gate['enabled']:st.warning(gate['reason'])
    if st.button('按严格规则自动入库',disabled=not gate['enabled'],key='paper_auto_save'):
        st.session_state.paper_auto_logs=import_automatic(jobs,workspace,license_note='用户上传，仅限本地科研核对，保留版权。')
        st.rerun()
    view=st.selectbox('处理队列',['待确认','规则预检通过（抽样复核）','引用数据','全部'],key='paper_queue')
    wanted={'待确认':'REVIEW','规则预检通过（抽样复核）':'AUTO_ELIGIBLE','引用数据':'CITATION'}.get(view)
    options=[i for i,j in enumerate(valid) if any(wanted is None or r['admission']['route']==wanted for r in j['draft'].candidates)]
    if not options:
        st.info('当前队列没有记录。');return
    active=st.selectbox('查看论文（仅列出当前队列的文件）',options,
        format_func=lambda i:valid[i]['draft'].filename,key=f'paper_file_{generation}_{view}')
    reviewed=[]
    for index,job in enumerate(valid):
        if index!=active:continue
        draft=job['draft']
        st.markdown('**'+draft.filename+'**')
        st.caption(f'{draft.pages} 页 · DOI：{draft.doi or "未识别，允许补填"} · PDF SHA256：{draft.sha256[:16]}…')
        for notice in draft.notices:st.caption(notice)
        if not draft.candidates:st.warning('没有找到可自动识别的标量值。可展开本地解析正文核对；扫描页需 OCR，曲线需后续人工数字化。')
        rows=[]
        visible=[r for r in draft.candidates if wanted is None or r['admission']['route']==wanted]
        for row in visible:
            rows.append({'selected':False,'candidate_id':row['candidate_id'],'page':row['location']['page'],
                'location':row['location']['label'],'status':row['status'],
                'ownership':row['attribution']['kind'],'ownership_confirmed':False,'review_note':'',
                **{key:str(row['values'].get(key) or '') for key in EDIT_FIELDS},
                'missing':'、'.join(FIELDS.get(k,k) for k in row['missing']),
                'warnings':'；'.join(row['warnings'])})
        if rows:
            st.caption('左右滚动可编辑数值、单位、方法、样品和 DOI。字段留空即 null。先勾选要保存的行，再核对下方原文。')
            configs={key:st.column_config.TextColumn(LABELS.get(key,key)) for key in EDIT_FIELDS}
            configs.update(selected=st.column_config.CheckboxColumn('选择保存'),
                ownership=st.column_config.TextColumn('数据归属'),
                ownership_confirmed=st.column_config.CheckboxColumn('人工确认本论文实验归属'),
                review_note=st.column_config.TextColumn('归属/配对原文核对依据'),
                method=st.column_config.SelectboxColumn('测量方法',options=['unknown','direct','indirect']),
                delta_t_basis=st.column_config.SelectboxColumn('ΔT 物理量口径',options=['reported_delta_t','signed_delta_t','positive_drop_magnitude','absolute_delta_t']),
                page=st.column_config.NumberColumn('页码'),location=st.column_config.TextColumn('来源位置'),
                status=st.column_config.TextColumn('状态'),missing=st.column_config.TextColumn('提取时缺失字段'),
                warnings=st.column_config.TextColumn('核对提示'),candidate_id=None)
            edited=st.data_editor(pd.DataFrame(rows),hide_index=True,num_rows='fixed',width='stretch',
                disabled=['candidate_id','page','location','status','missing','warnings','ownership'],column_config=configs,
                column_order=['selected','ownership','ownership_confirmed','review_note','formula','delta_t','delta_t_unit','delta_s','delta_s_unit','temperature','temperature_unit','e_delta','field_unit','method','page','location','status','missing','warnings']+[k for k in EDIT_FIELDS if k not in {'formula','delta_t','delta_t_unit','delta_s','delta_s_unit','temperature','temperature_unit','e_delta','field_unit','method'}],
                key=f'paper_editor_{generation}_{index}_{view}')
            current=[{'selected':bool(r['selected']),'candidate_id':r['candidate_id'],
                       'ownership_confirmed':bool(r['ownership_confirmed']),'review_note':str(r['review_note']).strip(),
                       'values':{k:str(r[k]).strip() if pd.notna(r[k]) else '' for k in EDIT_FIELDS}} for r in edited.to_dict('records')]
            reviewed.append((job,current))
            selected=st.selectbox('核对来源记录',range(len(rows)),key=f'paper_locator_{generation}_{index}_{view}',
                format_func=lambda i:f"第 {rows[i]['page']} 页 · {rows[i]['location']} · 候选 {i+1}")
            source=visible[selected]
            chosen_values=current[selected]['values']
            st.dataframe([{'字段':FIELDS[k],'当前值':chosen_values.get(k) or 'null（未提供）'} for k in
                ('formula','temperature','temperature_unit','e_delta','field_unit','delta_t','delta_t_unit','delta_s','delta_s_unit','method','sample_form','doi','delta_t_basis')],hide_index=True,height=250)
            missing=[FIELDS[k] for k in CORE if chosen_values.get(k) in (None,'','unknown')]
            st.caption('当前缺失字段：'+('、'.join(missing) or '无'))
            for warning in source['warnings']:st.caption(warning)
            st.caption('判定规则：'+source['attribution']['rule']+'；待处理原因：'+', '.join(source['admission']['reasons']))
            if source['admission']['route']=='CITATION':st.info('此条为引用线索；表中 DOI 留空，当前论文 DOI 仅在 enclosing_paper_doi 中保存。禁止作为本论文实验结果保存。')
            st.code(source['location']['quote'],language=None,wrap_lines=True)
            st.caption('来源位置按 PDF 物理页码（从 1 开始）；不等于期刊印刷页码。编辑值仍保留原提取值及来源片段。')
        with st.expander('本地原文、版权与提取草稿'):
            st.write(draft.copyright)
            if job.get('retained'):st.caption('本地草稿（含数字化待办）：'+job['retained']['draft_file'])
            st.json({'title':draft.title,'notices':draft.notices},expanded=False)
            page_num=st.number_input('查看解析页',min_value=1,max_value=draft.pages,value=1,key=f'paper_page_{generation}_{index}')
            st.text_area('本页解析文本',value='\n'.join(b['quote'] for b in draft.blocks if b['page']==page_num and b['kind']=='page'),height=220,disabled=True,key=f'paper_text_{generation}_{index}_{page_num}')
            st.download_button('下载本地提取草稿（含来源和数字化待办）',json.dumps(asdict(draft),ensure_ascii=False,indent=2),
                file_name=draft.sha256[:12]+'-draft.json',key=f'paper_export_{generation}_{index}')
        st.download_button('查看 / 下载原 PDF（本地）',job['content'],file_name=draft.filename,mime='application/pdf',key=f'paper_pdf_{generation}_{index}')
    license_note=st.text_input('本地使用与版权说明（必填）',value='用户提供的论文，仅限本地科研核对；保留原版权，不授权再分发。',key='paper_license')
    confirmed=st.checkbox('我已逐条核对所选数值、单位、物理量定义、测量方法及来源；未知字段继续留空。',key=f'paper_confirm_{generation}')
    review_signature=hashlib.sha256(json.dumps([signature,[(j['draft'].sha256,r) for j,r in reviewed],license_note,confirmed],sort_keys=True).encode()).hexdigest()
    if st.button('校验所选论文记录',key='paper_validate',disabled=not confirmed):
        prepared=[];errors=[]
        for job,items in reviewed:
            if not any(r['selected'] for r in items):continue
            try:
                p=prepare_review(job['content'],job['draft'],items,confirmed=confirmed,license_note=license_note)
                if p['errors']:errors.extend([{'文件':job['draft'].filename,**e} for e in p['errors']])
                else:prepared.append((job,p))
            except ValueError as error:errors.append({'文件':job['draft'].filename,'message':str(error)})
        if not prepared and not errors:errors.append({'message':'请选择至少一条已核对的实验记录。'})
        st.session_state.paper_prepared={'signature':review_signature,'items':prepared,'errors':errors}
    prepared=st.session_state.get('paper_prepared')
    if prepared and prepared['signature']!=review_signature:
        st.info('预览内容已修改，请重新校验后保存。');prepared=None
    if prepared:
        if prepared['errors']:
            st.error('校验未通过，尚未保存任何记录。');st.dataframe(prepared['errors'],hide_index=True)
        else:
            st.success('校验通过，请检查换算后的最终预览，再确认保存。')
            st.dataframe([record_preview(r.to_dict()) for _,p in prepared['items'] for r in p['records']],hide_index=True)
            with st.expander('完整字段与逐值来源关联'):
                st.json([r.to_dict() for _,p in prepared['items'] for r in p['records']],expanded=False)
        if st.button('确认保存论文记录到本机数据库',type='primary',disabled=bool(prepared['errors']),key='paper_save'):
            logs=[]
            try:
                for job,p in prepared['items']:logs.append(commit(job['content'],p,workspace))
                st.session_state.paper_saved=logs
                st.session_state.paper_saved_signature=review_signature
            except (ValueError,OSError) as error:
                st.error('保存未完成：'+str(error)+'；已完成文件数：'+str(len(logs))+'。可安全重试，已保存记录不会重复。')
    if st.session_state.get('paper_saved') and st.session_state.get('paper_saved_signature')==review_signature:
        logs=st.session_state.paper_saved
        st.success(f"已保存 {sum(l['inserted'] for l in logs if not l.get('already_imported'))} 条新记录；重复记录不再新增。可在下方“检索本机材料记录”中按化学式、DOI 或文件名检索。")
        st.caption('PDF 原件、来源和导入版本保存在当前本机工作区；没有启用模型训练、性能预测或 GitHub 发布。')
        reason=st.text_input('撤销本次论文导入的原因',key='paper_revoke_reason')
        if st.button('撤销本次论文导入',disabled=not reason.strip(),key='paper_revoke'):
            for log in logs:revoke_paper_import(workspace,log['import_id'],reason)
            st.success('已撤销本次论文新增记录；PDF、证据、原导入版本及撤销审计继续保留。')
