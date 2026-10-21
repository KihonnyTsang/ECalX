"""CSV/XLSX mapping, strict preview and immutable user-local dataset imports."""
from datetime import datetime, timezone
from dataclasses import fields as dataclass_fields
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import pandas as pd
from ecalx.data.schema import EvidenceRecord
from ecalx.data.repository import EvidenceRepository
from ecalx.data.quality import flag_possible_duplicates
try:
    from ecalx.data.schema import EXPERIMENTAL_RESPONSES
    from ecalx.data.quality import source_cell_identity, same_source_observation
except ImportError:  # Read-only compatibility with older personal checkouts.
    EXPERIMENTAL_RESPONSES = {'delta_T_jump_abs_K':('器件温跃幅值 |ΔTj|','K'),
                             'delta_S_vol_abs_kJ_K_m3':('体积熵变幅值 |ΔS|','kJ/(K·m³)')}
    def source_cell_identity(record):return None
    def same_source_observation(left,right):return False
from ecalx.materials.chemistry import parse_formula, display_formula
from . import SCIENCE_ROOT

FIELDS = {
 'formula':'材料化学式','composition':'组成说明（原样保留）','temperature':'温度', 'temperature_unit':'温度单位',
 'e_start':'电场起点','e_end':'电场终点','e_delta':'电场变化幅度 ΔE','e_max':'电场上限',
 'e_peak':'实际施加峰值（独立于上限 / ΔE）',
 'field_unit':'电场单位','field_path':'加载/撤场方向','method':'测量方法 direct / indirect',
 'doi':'来源 DOI','url':'来源 URL','sample_form':'样品形态','specimen_id':'样品身份',
 'protocol':'实验协议说明','delta_t':'实测 ΔT 标签','delta_t_unit':'ΔT 单位',
 'delta_s':'实测 ΔS 标签','delta_s_unit':'ΔS 单位','delta_t_basis':'ΔT 物理量口径',
 'sign_convention':'ΔT 符号约定','performance_name':'其他性能名称','performance_value':'其他性能值',
 'performance_unit':'其他性能单位','data_kind':'数据标记（如 SYNTHETIC_TEST_DATA）',
 'source_type':'来源类型','source_record_id':'本地实验记录编号','notes':'备注',
 'delta_T_jump_abs_K':'器件温跃幅值 |ΔTj| (K)',
 'delta_S_vol_abs_kJ_K_m3':'体积熵变幅值 |ΔS| (kJ/(K·m³))',
 'response_unit':'实验响应单位（保留原单位）',
 'e_reported':'原论文报告场级别（不代填 ΔE / 上限 / 峰值 / 端点）',
 'e_reported_kind':'原报告场的含义', 'field_level_role':'场级别 / 积分界限含义',
 'record_id':'来源记录 ID', 'source_id':'来源文件标识', 'original_row':'原始位置',
 'target_kind':'实验响应口径', 'value_derivation':'量热积分 / 温跃派生方式',
 'normalization_basis':'归一化口径', 'source_workbook':'原工作簿', 'source_sha256':'原文件 SHA256',
 'source_sheet':'原工作表', 'temperature_cell':'温度单元格', 'response_cell':'响应单元格',
 'raw_metadata_json':'原始元数据 JSON（保留许可与原表头）'}
TEMP_UNITS={'K':(1,0),'°C':(1,273.15),'C':(1,273.15),'℃':(1,273.15),'°F':(5/9,255.3722222222222)}
FIELD_UNITS={'kV/cm':1,'kV/mm':10,'MV/m':10,'V/m':0.00001,'V/cm':0.001,'V/mm':0.01,
             'V/μm':10,'V um-1':10}


def default_mapping(columns):
    aliases = {'formula':'formula_raw', 'composition':'nominal_composition',
               'temperature':'temperature_K', 'method':'measurement_method',
               'e_reported':'e_reported_kV_cm', 'sign_convention':'sign_convention'}
    return {key: key if key in columns else aliases[key] for key in FIELDS
            if key in columns or aliases.get(key) in columns}


def read_table(content,filename,sheet=0):
    if len(content)>20_000_000:raise ValueError('MVP 单个文件最大20 MB')
    suffix=Path(filename).suffix.lower()
    if suffix=='.csv':frame=pd.read_csv(BytesIO(content),dtype=str,keep_default_na=False,encoding='utf-8-sig')
    elif suffix=='.xlsx':frame=pd.read_excel(BytesIO(content),sheet_name=sheet,dtype=str,keep_default_na=False,engine='openpyxl')
    else:raise ValueError('请导出为 UTF-8 CSV 或 .xlsx Excel；SQLite / 数据库文件没有安全导入器，不会覆盖数据库或执行上传 SQL。旧 .xls 请另存为 .xlsx')
    if len(frame)>50000 or len(frame.columns)>100:raise ValueError('MVP 最多50000行、100列')
    if frame.columns.duplicated().any():raise ValueError('列名重复，请先改为唯一名称')
    return frame


def preview(content,filename,mapping,*,units=None,sheet=0):
    units=units or {};frame=read_table(content,filename,sheet);errors=[];warnings=[];records=[]
    if not mapping.get('formula'):raise ValueError('必须映射材料化学式列')
    if set(mapping)-FIELDS.keys():raise ValueError('未知映射字段')
    if any(c not in frame.columns for c in mapping.values() if c):raise ValueError('映射列不存在')
    columns=[c for c in mapping.values() if c]
    if len(columns)!=len(set(columns)):raise ValueError('一个源列不能映射为多个含义不同的字段')
    sha=hashlib.sha256(content).hexdigest()
    identity=hashlib.sha256(json.dumps([sha,mapping,units,sheet],sort_keys=True).encode()).hexdigest()
    for i,row in frame.iterrows():
        def val(key):
            column=mapping.get(key)
            text=str(row[column]).strip() if column else ''
            return text if text else None
        def number(key):
            raw=val(key)
            if raw is None:return None
            value=float(raw)
            if not math.isfinite(value):raise ValueError(key+' 必须为有限数值')
            return value
        def unit(key):return val(key) or units.get(key)
        try:
            formula=val('formula')
            if not formula:raise ValueError('材料化学式不能为空')
            amounts=parse_formula(formula)
            temperature=number('temperature');tu=unit('temperature_unit')
            if temperature is not None:
                if tu not in TEMP_UNITS:raise ValueError('温度数值需要明确单位 K / °C / °F')
                scale,offset=TEMP_UNITS[tu];temperature=temperature*scale+offset
            field={k:number(k) for k in ('e_start','e_end','e_delta','e_max','e_peak','e_reported')};fu=unit('field_unit')
            if any(v is not None for v in field.values()):
                if fu not in FIELD_UNITS:raise ValueError('电场数值需要明确支持的单位')
                field={k:v*FIELD_UNITS[fu] if v is not None else None for k,v in field.items()}
                if any(v is not None and v<0 for v in field.values()):raise ValueError('电场幅值不能为负')
            if field['e_start'] is not None and field['e_end'] is not None and field['e_delta'] is not None:
                if not math.isclose(abs(field['e_end']-field['e_start']),field['e_delta'],abs_tol=1e-8):
                    raise ValueError('电场起终点与 ΔE 矛盾')
            if field['e_max'] is not None and any(v is not None and v>field['e_max'] for k,v in field.items() if k in {'e_start','e_end','e_peak'}):
                raise ValueError('电场端点或实际峰值超过所填电场上限')
            if field['e_peak'] is not None and any(v is not None and v>field['e_peak'] for k,v in field.items() if k in {'e_start','e_end'}):
                raise ValueError('电场端点超过所填实际峰值')
            if field['e_max'] is not None and field['e_delta'] is not None and field['e_delta']>field['e_max']:
                raise ValueError('ΔE 超过非负电场的所填上限')
            dt=number('delta_t');ds=number('delta_s')
            responses = {key:number(key) for key in EXPERIMENTAL_RESPONSES}
            for key, value in responses.items():
                if value is not None and unit('response_unit') not in {None, EXPERIMENTAL_RESPONSES[key][1]}:
                    raise ValueError(key + ' 单位必须为 ' + EXPERIMENTAL_RESPONSES[key][1])
            positive_drop = val('delta_t_basis') == 'positive_drop_magnitude' or val('sign_convention') == 'positive_drop_magnitude'
            if val('delta_t_basis') == 'absolute_delta_t' and dt is not None and dt < 0:
                raise ValueError('绝对温变幅值不能为负；请核对原文是否为带符号 ΔT')
            if positive_drop and dt is not None and dt < 0:
                raise ValueError('正温降幅值不能为负；请保留原始定义或选择带符号 ΔT')
            if positive_drop and (val('delta_t_basis') == 'signed_delta_t' or val('sign_convention') == 'signed'):
                raise ValueError('ΔT 物理量口径与符号定义冲突')
            if dt is not None and unit('delta_t_unit') not in {'K','°C','C','℃'}:raise ValueError('ΔT标签需要明确温差单位 K / °C')
            if ds is not None:
                scale={'J/(kg K)':1,'J/kgK':1,'mJ/(g K)':1,'J/(g K)':1000}.get(unit('delta_s_unit'))
                if scale is None:raise ValueError('ΔS需要 J/(kg K)、mJ/(g K) 或 J/(g K) 单位')
                ds*=scale
            method=val('method') or 'unknown'
            if val('source_type') not in {None,'本人实验','文献','其他'}:
                raise ValueError('来源类型必须为 本人实验 / 文献 / 其他')
            method={'直接法':'direct','间接法':'indirect'}.get(method,method)
            path={'加载':'loading','撤场':'unloading'}.get(val('field_path'),val('field_path') or 'unknown')
            doi=val('doi')
            if doi:
                doi=doi.removeprefix('https://doi.org/').removeprefix('doi:').strip()
                if not __import__('re').fullmatch(r'10\.\d{4,9}/\S+',doi):raise ValueError('DOI格式无效；没有来源时请留空')
            url=val('url')
            if url:
                from urllib.parse import urlsplit
                parsed=urlsplit(url)
                if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('来源 URL 必须是无凭据的 HTTP(S) 地址')
            kind=val('data_kind');synthetic=kind=='SYNTHETIC_TEST_DATA'
            if 'SYNTHETIC_TEST_DATA' in filename.upper() and not synthetic:
                raise ValueError('示例文件的每行必须保留 SYNTHETIC_TEST_DATA 标记')
            prefix='SYNTHETIC_TEST_DATA-' if synthetic else 'user-'
            # Raw source bytes stay local; retain mapped original units and values.
            raw = json.loads(val('raw_metadata_json') or '{}')
            if not isinstance(raw, dict):raise ValueError('raw_metadata_json 必须为对象')
            raw.update({k:val(k) for k in mapping if k != 'raw_metadata_json'})
            raw['csv_original_row'] = {str(k):str(v) for k,v in row.items()}
            raw['unit_overrides']=units
            raw['actual_peak_kV_cm']=field['e_peak']
            # Older personal science checkouts lack dedicated delta/cap slots.
            # Preserve converted independent quantities as metadata there;
            # never migrate or alter the owner's scientific schema/database.
            supported = {f.name for f in dataclass_fields(EvidenceRecord)}
            independent = {'e_delta_kV_cm':field['e_delta'], 'e_max_kV_cm':field['e_max']}
            independent.update(responses)
            for key in ('target_kind','value_derivation','normalization_basis','field_level_role',
                        'source_workbook','source_sha256','source_sheet','temperature_cell','response_cell'):
                independent[key] = val(key)
            if any(v is not None for v in responses.values()) and not set(responses) <= supported:
                raise ValueError('当前科研数据模型缺少独立实验响应字段，请使用开发版；未转存到本征字段')
            extra = {k:v for k,v in independent.items() if k in supported}
            if len(extra) != len(independent):
                raw['normalized_fields'] = {k:v for k,v in independent.items() if k not in supported}
            raw['protocol_verification']='user_supplied_unverified'
            if kind == 'USER_ENTERED':
                raw.update(entry_method='USER_ENTERED', provenance='USER_SUPPLIED_UNVERIFIED')
            raw['protocol_fingerprint']=hashlib.sha256(json.dumps([val('protocol'),val('specimen_id'),method,
                val('sample_form'),val('delta_t_basis'),val('sign_convention'),field],ensure_ascii=False).encode()).hexdigest()
            flags=['user_supplied_unverified','sign_convention_unverified']
            if kind == 'USER_ENTERED':flags+=['USER_ENTERED','USER_SUPPLIED_UNVERIFIED']
            if synthetic:flags+=['SYNTHETIC_TEST_DATA']
            r=EvidenceRecord(record_id=val('record_id') or prefix+identity[:16]+'-'+str(i+2),formula_raw=formula,
                formula_normalized=display_formula(amounts),source_id=val('source_id') or prefix+sha[:16],doi=doi,url=val('url'),
                original_row=val('original_row') or 'row '+str(i+2),specimen_id=val('specimen_id'),sample_form=val('sample_form'),
                temperature_K=temperature,e_start_kV_cm=field['e_start'],e_end_kV_cm=field['e_end'],
                e_reported_kV_cm=field['e_reported'] if field['e_reported'] is not None else field['e_delta'] if field['e_delta'] is not None else field['e_max'],
                e_reported_kind=(val('e_reported_kind') or 'reported') if field['e_reported'] is not None else 'delta' if field['e_delta'] is not None else 'maximum' if field['e_max'] is not None else 'unknown',
                field_path=path,measurement_method=method,delta_T_ad_K=dt,delta_S_J_kgK=ds,
                delta_t_basis=val('delta_t_basis') or 'unspecified_user_quantity',
                delta_t_sign_convention=val('sign_convention') or 'unknown',
                raw_metadata_json=raw,quality_flags=flags,**extra).validate()
            records.append(r)
            if not (doi or r.url):warnings.append({'row':i+2,'message':'没有可追溯来源，只能检索，不能用作候选生成种子'})
        except (ValueError,TypeError) as e:
            errors.append({'row':i+2,'message':str(e)})
    return {'records':records,'errors':errors,'warnings':warnings,'import_id':identity,
            'sha256':sha,'mapping':mapping,'units':units,'sheet':sheet,'filename':Path(filename).name,
            'rows':len(frame),'preview':[r.to_dict() for r in records[:30]]}


def commit(content,prepared,workspace=None):
    if prepared['errors']:raise ValueError('存在错误行；请修正后完整导入，未写入任何记录')
    if not prepared['records']:raise ValueError('没有可导入记录')
    if hashlib.sha256(content).hexdigest()!=prepared['sha256']:raise ValueError('文件已变化，请重新预览')
    root=Path(workspace or SCIENCE_ROOT);repo=EvidenceRepository(root/'data/evidence.sqlite3')
    # Namespace imports by content+mapping+units; repeat imports are idempotent.
    iid=prepared['import_id'];raw=root/'raw';raw.mkdir(parents=True,exist_ok=True)
    target=raw/(prepared['sha256']+Path(prepared['filename']).suffix.lower())
    with repo._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        found=db.execute('SELECT payload FROM imports WHERE import_id=?',(iid,)).fetchone()
        if found:return {**json.loads(found[0]),'already_imported':True}
        timestamp = datetime.now(timezone.utc).isoformat()
        existing = {r[0]:json.loads(r[1]) for r in db.execute('SELECT record_id,payload FROM records')}
        locations = {source_cell_identity(r):r for r in
                     (EvidenceRecord(**p) for p in existing.values()) if source_cell_identity(r)}
        flag_possible_duplicates(prepared['records'], [EvidenceRecord(**p) for p in existing.values()])
        inserted=0; skipped=[]; paper_snapshots={}
        for r in prepared['records']:
            location = source_cell_identity(r)
            if location and location in locations:
                if not same_source_observation(locations[location], r):
                    raise ValueError('同一来源单元格数值或物理口径冲突，未覆盖：'+r.record_id)
                skipped.append(r.record_id)
                continue
            if r.record_id in existing:
                prior=dict(existing[r.record_id]); current=r.to_dict()
                for value in (prior,current):
                    value.pop('import_id',None)
                    value.pop('quality_flags',None)
                if prior!=current:raise ValueError('记录编号已存在且内容不同，未覆盖：'+r.record_id)
                skipped.append(r.record_id)
                continue
            r.import_id=iid
            if r.raw_metadata_json.get('entry_method') == 'USER_ENTERED':
                r.raw_metadata_json['entered_at_utc'] = timestamp
            db.execute('INSERT INTO records VALUES(?,?)',(r.record_id,json.dumps(r.to_dict(),ensure_ascii=False,allow_nan=False)))
            if r.raw_metadata_json.get('paper_candidate_id'):
                paper_snapshots[r.record_id]=hashlib.sha256(json.dumps(r.to_dict(),sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            inserted+=1
            if location:locations[location]=r
        for source_id,payload in prepared.get('sources',{}).items():
            db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)',(source_id,iid,json.dumps(payload,ensure_ascii=False)))
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=prepared['sha256']:raise ValueError('原始文件校验失败')
        else:
            with target.open('xb') as f:f.write(content)
        version_payload=json.dumps([json.loads(r[0]) for r in db.execute('SELECT payload FROM records ORDER BY record_id')],sort_keys=True,ensure_ascii=False)
        log={k:prepared[k] for k in ('import_id','sha256','mapping','units','sheet','filename','rows')}
        log.update(timestamp_utc=timestamp,inserted=inserted,duplicate_ids_skipped=skipped,
            data_version='sha256:'+hashlib.sha256(version_payload.encode()).hexdigest(),
            raw_file=target.relative_to(root).as_posix(), predictions_enabled=False)
        if paper_snapshots:log['paper_record_snapshots']=paper_snapshots
        db.execute('INSERT INTO imports VALUES(?,?,?)',(iid,prepared['sha256'],json.dumps(log,ensure_ascii=False)))
    return log


def manual_content(values):
    """Stable one-row CSV lets manual entries share every bulk validation rule.

    Timestamp is assigned on first commit, so repeated clicks and page reloads
    cannot produce a second import for the same entry.
    """
    row = {key: str(values[key]).strip() for key in FIELDS if values.get(key) is not None}
    row['data_kind'] = 'USER_ENTERED'
    return pd.DataFrame([row], columns=sorted(row)).to_csv(index=False, lineterminator='\n').encode('utf-8')


def preview_manual(values):
    content = manual_content(values)
    columns = read_table(content, 'manual-entry.csv').columns
    return content, preview(content, 'manual-entry.csv', {key:key for key in columns})
