"""CSV/XLSX mapping, strict preview and immutable user-local dataset imports."""
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import pandas as pd
from ecalx.data.schema import EvidenceRecord
from ecalx.data.repository import EvidenceRepository
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
 'performance_unit':'其他性能单位','data_kind':'数据标记（如 SYNTHETIC_TEST_DATA）'}
TEMP_UNITS={'K':(1,0),'°C':(1,273.15),'C':(1,273.15),'℃':(1,273.15),'°F':(5/9,255.3722222222222)}
FIELD_UNITS={'kV/cm':1,'kV/mm':10,'MV/m':10,'V/m':0.00001,'V/cm':0.001,'V/mm':0.01}


def read_table(content,filename,sheet=0):
    if len(content)>20_000_000:raise ValueError('MVP 单个文件最大20 MB')
    suffix=Path(filename).suffix.lower()
    if suffix=='.csv':frame=pd.read_csv(BytesIO(content),dtype=str,keep_default_na=False,encoding='utf-8-sig')
    elif suffix=='.xlsx':frame=pd.read_excel(BytesIO(content),sheet_name=sheet,dtype=str,keep_default_na=False,engine='openpyxl')
    else:raise ValueError('支持 UTF-8 CSV 或 .xlsx Excel；旧 .xls 请另存为 .xlsx')
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
            field={k:number(k) for k in ('e_start','e_end','e_delta','e_max','e_peak')};fu=unit('field_unit')
            if any(v is not None for v in field.values()):
                if fu not in FIELD_UNITS:raise ValueError('电场数值需要明确支持的单位')
                field={k:v*FIELD_UNITS[fu] if v is not None else None for k,v in field.items()}
                if any(v is not None and v<0 for v in field.values()):raise ValueError('电场幅值不能为负')
            if field['e_start'] is not None and field['e_end'] is not None and field['e_delta'] is not None:
                if not math.isclose(abs(field['e_end']-field['e_start']),field['e_delta'],abs_tol=1e-8):
                    raise ValueError('电场起终点与 ΔE 矛盾')
            if field['e_max'] is not None and any(v is not None and v>field['e_max'] for k,v in field.items() if k in {'e_start','e_end','e_peak'}):
                raise ValueError('电场端点或实际峰值超过所填电场上限')
            dt=number('delta_t');ds=number('delta_s')
            if dt is not None and unit('delta_t_unit') not in {'K','°C','C','℃'}:raise ValueError('ΔT标签需要明确温差单位 K / °C')
            if ds is not None:
                scale={'J/(kg K)':1,'J/kgK':1,'mJ/(g K)':1,'J/(g K)':1000}.get(unit('delta_s_unit'))
                if scale is None:raise ValueError('ΔS需要 J/(kg K)、mJ/(g K) 或 J/(g K) 单位')
                ds*=scale
            method=val('method') or 'unknown'
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
            raw={k:val(k) for k in mapping};raw['unit_overrides']=units
            raw['actual_peak_kV_cm']=field['e_peak']
            raw['protocol_verification']='user_supplied_unverified'
            raw['protocol_fingerprint']=hashlib.sha256(json.dumps([val('protocol'),val('specimen_id'),method,
                val('sample_form'),val('delta_t_basis'),val('sign_convention'),field],ensure_ascii=False).encode()).hexdigest()
            flags=['user_supplied_unverified','sign_convention_unverified']
            if synthetic:flags+=['SYNTHETIC_TEST_DATA']
            r=EvidenceRecord(record_id=prefix+identity[:16]+'-'+str(i+2),formula_raw=formula,
                formula_normalized=display_formula(amounts),source_id=prefix+sha[:16],doi=doi,url=val('url'),
                original_row='row '+str(i+2),specimen_id=val('specimen_id'),sample_form=val('sample_form'),
                temperature_K=temperature,e_start_kV_cm=field['e_start'],e_end_kV_cm=field['e_end'],
                e_delta_kV_cm=field['e_delta'],e_max_kV_cm=field['e_max'],
                e_reported_kV_cm=field['e_delta'] if field['e_delta'] is not None else field['e_max'],
                e_reported_kind='delta' if field['e_delta'] is not None else 'maximum' if field['e_max'] is not None else 'unknown',
                field_path=path,measurement_method=method,delta_T_ad_K=dt,delta_S_J_kgK=ds,
                delta_t_basis=val('delta_t_basis') or 'unspecified_user_quantity',
                delta_t_sign_convention=val('sign_convention') or 'unknown',
                raw_metadata_json=raw,quality_flags=flags).validate()
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
        for r in prepared['records']:
            r.import_id=iid
            db.execute('INSERT INTO records VALUES(?,?)',(r.record_id,json.dumps(r.to_dict(),ensure_ascii=False,allow_nan=False)))
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=prepared['sha256']:raise ValueError('原始文件校验失败')
        else:
            with target.open('xb') as f:f.write(content)
        version_payload=json.dumps([json.loads(r[0]) for r in db.execute('SELECT payload FROM records ORDER BY record_id')],sort_keys=True,ensure_ascii=False)
        log={k:prepared[k] for k in ('import_id','sha256','mapping','units','sheet','filename','rows')}
        log.update(timestamp_utc=datetime.now(timezone.utc).isoformat(),inserted=len(prepared['records']),
            data_version='sha256:'+hashlib.sha256(version_payload.encode()).hexdigest(),
            raw_file=target.relative_to(root).as_posix(), predictions_enabled=False)
        db.execute('INSERT INTO imports VALUES(?,?,?)',(iid,prepared['sha256'],json.dumps(log,ensure_ascii=False)))
    return log
