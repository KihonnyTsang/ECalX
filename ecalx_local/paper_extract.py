"""Conservative, offline PDF evidence extraction. No network/model dependencies.

Candidates carry separate attribution, pairing and strict admission decisions.
Only explicit scalar values and geometrically aligned table rows are parsed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pandas as pd

from .import_data import FIELDS, preview, commit
from . import PERSONAL
from ecalx.materials.chemistry import parse_formula

EXTRACTOR_VERSION = 'offline-rules-3'
MAX_BYTES = 20_000_000
MAX_PAGES = 100
NUM = r'[+\-]?(?:\d+(?:\.\d*)?|\.\d+)'
FU = r'(?:kV\s*/\s*cm|kV\s*/\s*mm|MV\s*/\s*m|V\s*/\s*cm|V\s*/\s*m|kV\s*cm-1|MV\s*m-1|V\s*[μµ]m-1|V\s*/\s*[μµ]m)'
TU = r'(?:°\s*C|oC|℃|K)'
SU = r'(?:J\s*/\s*kg\s*/?\s*K|J\s*/\s*\(kg\s*K\)|J\s*kg-1\s*K-1|mJ\s*/\s*\(g\s*K\)|J\s*/\s*g\s*/\s*K)'
QUANTITIES = {
    'delta_t': rf'(?:\|?Δ\s*T(?:_?(?:ad|EC|max|j))?\|?|temperature (?:change|drop)|cooling magnitude)\s*(?:(?:of|was|were|is|=|~|≈|reaches?|achieved|a|high|maximum)\s*)*({NUM})\s*(K|°C)',
    'delta_s': rf'(?:\|?Δ\s*S\|?|entropy change)\s*(?:(?:of|was|were|is|=|~|≈|reaches?|a|high|maximum)\s*)*({NUM})\s*({SU})',
}
CORE = ('formula','temperature','e_delta','delta_t','delta_s','method','sample_form','doi')


def norm(text):
    text = text.translate(str.maketrans('₀₁₂₃₄₅₆₇₈₉', '0123456789'))
    text = text.translate(str.maketrans({'\uf044':'Δ','\uf054':'T','\uf045':'E','\uf053':'S','◦':'°'}))
    text = unicodedata.normalize('NFKC', text).replace('∆','Δ').replace('−','-').replace('\u00ad','')
    return re.sub(r'\s+', ' ', re.sub(r'([a-z])-\s*\n\s*([a-z])',r'\1\2',text)).strip()


def unit(text):
    text = norm(text).replace(' ','').replace('oC','°C')
    if text in {'J/kg/K','J/kgK','Jkg-1K-1','J/(kgK)'}: return 'J/(kg K)'
    if text in {'kVcm-1','MVm-1'}:return {'kVcm-1':'kV/cm','MVm-1':'MV/m'}[text]
    if text in {'Vμm-1','Vµm-1','V/μm','V/µm'}:return 'kV/mm'
    if text == 'J/g/K': return 'J/(g K)'
    if text == 'mJ/(gK)': return 'mJ/(g K)'
    return text


def formulas(text):
    found=[]
    for match in re.finditer(r'(?<![A-Za-z0-9])(?:\([A-Z][A-Za-z0-9.()]*\)\d*|[A-Z][a-z]?\d*(?:\.\d+)?){2,}(?![A-Za-z])',text):
        candidate=match.group()
        try:
            amounts=parse_formula(candidate)
            if len(amounts)>=2 and any(c.isdigit() for c in candidate): found.append(candidate)
        except ValueError: pass
    return list(dict.fromkeys(found))


def method_of(text):
    methods=set(re.findall(r'\b(indirect|direct)\b',text.lower()))
    return next(iter(methods)) if len(methods)==1 else 'unknown'


def basis_of(text):
    low=text.lower()
    if re.search(r'positive.*(?:cooling|drop)|cooling magnitude|temperature drop',low):
        return 'positive_drop_magnitude', 'positive_drop_magnitude'
    if re.search(r'\|\s*ΔT\s*\||absolute.*(?:ΔT|temperature)',text): return 'absolute_delta_t','absolute_magnitude'
    if re.search(r'ΔT\s*(?:=|of|was)?\s*-\d',text): return 'signed_delta_t','signed'
    return 'reported_delta_t','source_reported_unverified'


@dataclass
class PaperDraft:
    filename: str
    sha256: str
    pages: int
    title: str
    doi: str | None
    copyright: str
    candidates: list[dict] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)


def location(page,kind,label,quote,bbox=None):
    return {'page':page,'kind':kind,'label':label,'quote':quote,'bbox':list(bbox) if bbox else None}


def draft_row(values, loc, doi, warnings=(), reference=None):
    values={k:v for k,v in values.items() if v is not None}
    # The enclosing PDF DOI is never the primary source of a cited measurement.
    if doi: values['doi']=doi
    values.setdefault('method','unknown')
    values['source_type']='文献'
    missing=[k for k in CORE if values.get(k) in (None,'','unknown')]
    return {'values':values,'location':loc,'field_sources':{k:dict(loc) for k in values},
            'missing':missing,'warnings':list(warnings),'reference':reference,
            'status':'待核对','candidate_id':'',
            'pairing':{'status':'UNRESOLVED','rule':'no_scalar_pairing'}}


def _table_rows(page, page_no, doi):
    """Geometric rows, including centered/multiline headers; never fill down."""
    words=page.extract_words(x_tolerance=1,y_tolerance=3)
    lines=[]
    for word in sorted(words,key=lambda w:w['top']):
        if not lines or abs(word['top']-lines[-1][0]['top'])>5:lines.append([word])
        else:lines[-1].append(word)
    result=[];boxes=[]
    for h,head in enumerate(lines):
        if not any(re.fullmatch(r'Material|Composition|Formula',norm(w['text']),re.I) for w in head):continue
        # A header can occupy several baselines, but must stop before data.
        header_lines=[]
        for line in lines[max(0,h-1):h+4]:
            text=' '.join(norm(w['text']) for w in line)
            if line is not head and formulas(text) and len(re.findall(r'(?<![A-Za-z])'+NUM+r'(?![A-Za-z])',text))>=3:break
            header_lines.append(line)
        header_words=[w for line in header_lines for w in line]
        keys=[]
        for w in header_words:
            t=norm(w['text']).strip('|');key=None
            if re.fullmatch(r'Material|Composition|Formula',t,re.I):key='formula'
            elif re.match(r'Δ[TS]/',t):key='ignore'
            elif re.match(r'ΔT(?:EC|ad|max|j)?(?:\b|[|(/])',t):key='delta_t'
            elif re.match(r'ΔS(?:\b|[|(/])',t):key='delta_s'
            elif re.match(r'ΔE(?:\b|[|(/])',t):key='e_delta'
            elif re.match(r'T[cC](?:\b|[|(/])',t):key='tc'
            elif re.match(r'(?:TmaxEC|Temperature|T)(?:\b|[|(/])',t,re.I):key='temperature'
            elif re.match(r'Method|Measurement',t,re.I):key='method'
            elif re.match(r'Ref',t,re.I):key='reference'
            elif re.match(r'Q[(/]',t):key='ignore'
            if key:keys.append(((w['x0']+w['x1'])/2,key,w))
        keys.sort(key=lambda x:x[0])
        if 'formula' not in [k[1] for k in keys] or not any(k[1] in {'delta_t','delta_s'} for k in keys):continue
        if len([k for k in keys if k[1]=='formula'])!=1:continue
        # Duplicate physical quantity headings cannot define a simple scalar row.
        if any(sum(k[1]==name for k in keys)>1 for name in ('delta_t','delta_s','e_delta','temperature')):continue
        starts=[0]+[(a[0]+b[0])/2 for a,b in zip(keys,keys[1:])]
        ends=starts[1:]+[page.width]
        columns=[]
        for (center,key,w),left,right in zip(keys,starts,ends):
            hw=sorted([x for x in header_words if left<=(x['x0']+x['x1'])/2<right],key=lambda x:(x['top'],x['x0']))
            text=' '.join(norm(x['text']) for x in hw);u=None
            if key=='temperature':
                if re.search(r'°\s*C|◦\s*C',text):u='°C'
                elif re.search(r'\bK\b|\(K\)',text):u='K'
            elif key=='e_delta':
                m=re.search(FU,text,re.I);u=unit(m.group()) if m else None
            elif key=='delta_t':
                if re.search(r'\bK\b|\(K\)',text):u='K'
                elif re.search(r'°\s*C|◦\s*C',text):u='°C'
            elif key=='delta_s':
                m=re.search(SU,text,re.I);u=unit(m.group()) if m else None
            columns.append((left,right,key,u,hw))
        preceding=page.crop((0,0,page.width,min(w['top'] for w in header_words))).extract_text() or ''
        labels=list(re.finditer(r'Table\s*(\d+|[IVX]+)',preceding,re.I))
        label='Table '+labels[-1].group(1) if labels else '表格（编号待核对）'
        caption=norm(preceding[labels[-1].start():]) if labels else ''
        header=' '.join(norm(w['text']) for w in sorted(header_words,key=lambda x:(x['top'],x['x0'])))
        table_top=min(w['top'] for w in header_words);header_bottom=max(w['bottom'] for w in header_words);bottom=header_bottom;data_index=0
        for row_no,line in enumerate(lines[h+1:],1):
            y=min(w['top'] for w in line)
            if y<header_bottom-2:continue
            if y>table_top+450:break
            cells={};cell_words={}
            for left,right,key,u,hw in columns:
                ws=sorted([w for w in line if left<=(w['x0']+w['x1'])/2<right],key=lambda w:w['x0'])
                cells[key]=' '.join(norm(w['text']) for w in ws);cell_words[key]=ws
            if not cells.get('formula'):continue
            numeric={key:cells.get(key,'').replace(' ','') for key in ('temperature','e_delta','delta_t','delta_s')}
            if not any(re.fullmatch(NUM,numeric[k]) for k in ('delta_t','delta_s')):
                if bottom>header_bottom and y>bottom+15:break
                continue
            data_index+=1
            values={'composition':cells['formula']};warnings=[]
            for _,_,key,u,_ in columns:
                if key in numeric and re.fullmatch(NUM,numeric[key]):
                    values[key]=numeric[key]
                    uk={'temperature':'temperature_unit','e_delta':'field_unit'}.get(key,key+'_unit')
                    if u:values[uk]=u
                    else:warnings.append('表格单位不明：'+key)
            fs=formulas(cells['formula'].replace(' ',''))
            if len(fs)==1 and fs[0]==cells['formula'].replace(' ',''):values['formula']=fs[0]
            else:warnings.append('组成缩写/通式/复合组分需核对；未推断化学计量。')
            if cells.get('method'):
                method=method_of(cells['method'])
                if method=='unknown' and re.fullmatch(r'thermocouple|calorimeter|DSC|Pt100|thermistor|thermoelement',cells['method'],re.I):method='direct'
                values['method']=method
            if any(c[2]=='tc' for c in columns):warnings.append('Tc 不是测试温度。')
            # Physical definitions remain attached to the table caption; maxima
            # of separate columns are not presumed simultaneous measurements.
            if re.search(r'peak values.*ΔT.*ΔS|maximum.*ΔT.*maximum.*ΔS',caption,re.I):warnings.append('多物理量最大值不保证同条件；需核对配对。')
            basis,sign=basis_of(header);values.update(delta_t_basis=basis,sign_convention=sign)
            ordered=sorted(line,key=lambda w:w['x0']);line_text=' '.join(norm(w['text']) for w in ordered)
            quote=header+'\n'+line_text
            loc=location(page_no,'table',f'{label} · 数据行 {data_index}',quote,
                         (0,y,page.width,max(w['bottom'] for w in line)))
            row=draft_row(values,loc,doi,warnings,cells.get('reference'))
            row['pairing']={'status':'EXPLICIT','rule':'aligned_table_row','row':data_index}
            row['table_context']=location(page_no,'table_caption',label,caption)
            def rect(ws):return [min(w['x0'] for w in ws),min(w['top'] for w in ws),max(w['x1'] for w in ws),max(w['bottom'] for w in ws)]
            for _,_,key,u,hw in columns:
                ws=cell_words.get(key,[])
                if key not in values or not ws:continue
                token=cells[key];pos=len(header)+1+sum(len(norm(w['text']))+1 for w in ordered[:ordered.index(ws[0])])
                row['field_sources'][key]={**loc,'span':[pos,pos+len(token)],'text':token,'bbox':rect(ws)}
                if key=='formula':row['field_sources']['composition']=row['field_sources'][key]
                uk={'temperature':'temperature_unit','e_delta':'field_unit'}.get(key,key+'_unit')
                if u and uk in values and hw:
                    token=norm(hw[0]['text']);pos=header.find(token)
                    row['field_sources'][uk]={**loc,'span':[pos,pos+len(token)],'text':token,'bbox':rect(hw)}
            result.append(row);bottom=loc['bbox'][3]
        if bottom>header_bottom:boxes.append((0,table_top-2,page.width,bottom+2))
    return result,boxes


def _locate_fields(row,page):
    """Map normalized quote offsets back to actual PDF word rectangles.

    If PDF reading order cannot be aligned exactly, leave bbox null and abstain
    from automatic admission. A page-sized rectangle is not a field locator.
    """
    loc=row['location']
    if loc['kind']=='table':return
    words=page.crop(tuple(loc['bbox'])).extract_words(x_tolerance=1,y_tolerance=3) if loc.get('bbox') else page.extract_words(x_tolerance=1,y_tolerance=3)
    compact='';indices=[]
    for index,w in enumerate(words):
        token=norm(w['text']).replace(' ','')
        compact+=token;indices.extend([index]*len(token))
    quote=loc['quote'];needle=quote.replace(' ','')
    start=compact.find(needle)
    unique=start>=0 and compact.find(needle,start+1)<0
    for key,src in row['field_sources'].items():
        if not src.get('span'):
            src['bbox']=None
            continue
        a,b=src['span']
        src['bbox']=None
        if not unique:continue
        left=start+len(quote[:a].replace(' ',''));right=start+len(quote[:b].replace(' ',''))
        if right<=left or right>len(indices):continue
        selected=[words[i] for i in sorted(set(indices[left:right]))]
        src['bbox']=[min(w['x0'] for w in selected),min(w['top'] for w in selected),
                     max(w['x1'] for w in selected),max(w['bottom'] for w in selected)]


def _paragraphs(cropped):
    """Use actual line gaps/indents so a caption cannot consume the next body."""
    lines=cropped.extract_text_lines(x_tolerance=1,y_tolerance=3)
    blocks=[];current=[]
    for line in lines:
        text=line['text'].strip()
        if not text:continue
        split=False
        if current:
            prev=current[-1]
            gap=line['top']-prev['bottom']
            height=max(6,prev['bottom']-prev['top'])
            heading=bool(re.match(r'(?:Fig(?:ure)?\.?\s*\d+|Table\s*\d+|References\b|Supplementary (?:Information|Materials|Notes?)|\d+\.\s+[A-Z])',text,re.I))
            indent=line['x0']>min(x['x0'] for x in current)+6 and gap>0
            split=heading or gap>max(5,height*.65) or indent
        if split:blocks.append(current);current=[]
        current.append(line)
    if current:blocks.append(current)
    return [('\n'.join(x['text'] for x in b),
        [min(x['x0'] for x in b),min(x['top'] for x in b),max(x['x1'] for x in b),max(x['bottom'] for x in b)]) for b in blocks]


def _resolve_aliases(draft,pages,texts):
    """Only explicit, unique formula (ALIAS) definitions; no x/y substitution.

    Definition and usage keep separate locations, including supplementary pages.
    Conflicting definitions or aliased mixtures remain unresolved evidence.
    """
    definitions={}
    for page_no,(page,raw) in enumerate(zip(pages,texts),1):
        text=norm(raw)
        for formula in formulas(text):
            for m in re.finditer(re.escape(formula)+r'\s*\(([A-Z][A-Za-z0-9-]{1,15})\)',text):
                alias=m.group(1)
                loc=location(page_no,'definition','明确组成缩写定义',text)
                src=_source(loc,[m.start(),m.start()+len(formula)],formula)
                hits=page.search(re.escape(formula),regex=True)
                if len(hits)==1:
                    hit=hits[0];src['bbox']=[hit['x0'],hit['top'],hit['x1'],hit['bottom']]
                definitions.setdefault(alias,[]).append((formula,src))
    for row in draft.candidates:
        if row['values'].get('formula') or row.get('evidence_only'):continue
        quote=row.get('identity_scope',row['values'].get('composition') or row['location']['quote'])
        hits=[]
        for alias,defs in definitions.items():
            # A hyphenated modified sample is not the undoped base material.
            m=re.search(r'(?<![A-Za-z0-9+-])'+re.escape(alias)+r'(?![A-Za-z0-9+-]|\s*\+)',quote)
            if m and len({f for f,_ in defs})==1:hits.append((alias,defs[0],m.span()))
        if len(hits)!=1:continue
        alias,(formula,src),span=hits[0]
        # Other unresolved material names/dopants must not silently disappear.
        if re.search(r'\b(?:doped|wt\s*%|mol\s*%|substitut|x\s*=|y\s*=)',quote,re.I):continue
        row['values']['formula']=formula
        row['values'].setdefault('composition',alias)
        row['field_sources']['formula']={**src,'origin':'explicit_alias_definition',
            'alias':alias,'usage_location':_source(row['location'],
                [row['location']['quote'].find(alias),row['location']['quote'].find(alias)+len(alias)],alias)}
        row['alias_resolution']={'alias':alias,'formula':formula,'definition':src}
        row['warnings']=[w for w in row['warnings'] if w!='本片段没有明确化学式；缩写或跨句组成需核对。']


def _series(text, label, units):
    """Ordered values, supporting repeated or shared units; never Cartesian products."""
    lead = r'\s*(?:(?:of|was|were|is|are|=|~|≈|reaches?|achieved|a|high|maximum)\s*)*'
    result=[]
    pattern = rf'(?:{label}){lead}(?P<nums>{NUM}(?:\s*(?:,\s*(?:and\s*)?|and\s+){NUM})*)\s*(?P<u>{units})'
    for m in re.finditer(pattern,text,re.I):
        for num in re.finditer(NUM,m.group('nums')):
            result.append({'value':num.group(),'unit':unit(m.group('u')),
                'span':[m.start('nums')+num.start(),m.start('nums')+num.end()],
                'unit_span':[m.start('u'),m.end('u')], 'full_span':[m.start(),m.end()]})
        # Continue a list with repeated units, but never jump over another label.
        end=m.end()
        while True:
            tail=re.match(rf'\s*(?:,\s*(?:and\s*)?|and\s+)({NUM})\s*({units})',text[end:],re.I)
            if not tail:break
            result.append({'value':tail.group(1),'unit':unit(tail.group(2)),
                'span':[end+tail.start(1),end+tail.end(1)],
                'unit_span':[end+tail.start(2),end+tail.end(2)],'full_span':[end,end+tail.end()]})
            end+=tail.end()
    return result


def _source(loc, span, value=None):
    return {**loc,'span':list(span),'text':loc['quote'][span[0]:span[1]],'value':value}


def scalar_candidates(text,loc,doi):
    text=norm(text);loc={**loc,'quote':text}
    labels={'delta_t':r'\|?Δ\s*T(?:_?(?:ad|EC|max|j))?\|?|(?:adiabatic )?temperature (?:change|drop)|cooling magnitude',
            'delta_s':r'\|?Δ\s*S\|?|(?:isothermal )?entropy change'}
    quantities={key:_series(text,label,SU if key=='delta_s' else r'K|°\s*C') for key,label in labels.items()}
    if not any(quantities.values()):
        if re.search(r'Δ\s*[TS]\s*[<>≤≥]',text):
            return [draft_row({},loc,doi,['仅有界限或范围，未提取为精确实验数值。'])]
        return []
    sources={};base={};warnings=[]
    identity_text=re.split(r',?\s+(?:which|whereas|compared (?:to|with)|rather than)\b',text,maxsplit=1,flags=re.I)[0]
    fs=formulas(identity_text)
    formula_series=[{'value':f,'span':[identity_text.index(f),identity_text.index(f)+len(f)]} for f in fs]
    base['method']=method_of(text)
    mm=re.search(r'\b(?:indirect|direct)\b',text,re.I)
    if mm and base['method']!='unknown':sources['method']=_source(loc,mm.span(),base['method'])
    sf=list(re.finditer(r'\b(?:single crystal|ceramic|thin film|thick film|multilayer)[s]?\b',identity_text,re.I))
    if len(set(x.group().lower() for x in sf))==1:
        base['sample_form']=sf[0].group();sources['sample_form']=_source(loc,sf[0].span(),sf[0].group())
    sm=re.search(r'\b(?:sample|specimen)\s+(?:ID\s*)?[#:]\s*([A-Za-z0-9_-]+)',identity_text,re.I)
    if sm:base['specimen_id']=sm.group(1);sources['specimen_id']=_source(loc,sm.span(1),sm.group(1))
    basis,sign=basis_of(text);base.update(delta_t_basis=basis,sign_convention=sign)
    if re.search(r'(?:Δ\s*[TS]|temperature change|entropy change)\s*(?:[<>≤≥]|at least|up to)',text,re.I):
        return [draft_row(base,loc,doi,['仅有界限或范围，未提取为精确实验数值。'])]
    temps=_series(text,r'\bat|\btemperature(?: of)?|\bT\s*=',TU)
    excluded=[q['full_span'] for qs in quantities.values() for q in qs]
    temps=[m for m in temps if not any(a<=m['span'][0]<b for a,b in excluded)]
    fields=_series(text,r'Δ\s*E|(?:electric )?field (?:change|variation)|field-change',FU)
    peaks=_series(text,r'(?:applied )?(?:electric )?field(?: of)?|under|E_?(?:max|peak)\s*=',FU)
    if not fields and len(peaks)==1:
        base.update(e_peak=peaks[0]['value'],field_unit=peaks[0]['unit'])
        sources['e_peak']=_source(loc,peaks[0]['span'],peaks[0]['value'])
        warnings.append('仅报告施加电场，未解释为 ΔE，未补齐端点。')
    sizes=[len(q) for q in quantities.values() if q]+[len(temps),len(fields),len(formula_series)]
    n=max(sizes)
    multi=n>1
    # All outcome vectors must have the same cardinality. A singleton condition
    # may be shared, but two independent varying axes require explicit respectively.
    paired=(not multi or (bool(re.search(r'\brespectively\b',text,re.I)) and
        all(len(q)==n for q in quantities.values() if q) and
        all(len(q) in (0,1,n) for q in (temps,fields,formula_series))))
    if not paired:
        row=draft_row(base,loc,doi,warnings+['多组材料/温度/电场/性能配对不明确；未生成实验标签。'])
        row['pairing']={'status':'UNRESOLVED','rule':'cardinality_or_respectively_not_proven'}
        row['field_sources']=sources
        return [row]
    if not formula_series:warnings.append('本片段没有明确化学式；缩写或跨句组成需核对。')
    if re.search(r'\bx\s*=|\by\s*=|\d+\s*(?:wt|mol)\s*%',identity_text,re.I):
        warnings.append('含可变组成或掺杂条件，未推断具体样品计量。')
    out=[]
    for i in range(n):
        values=dict(base);src=dict(sources)
        for key,seq in [('formula',formula_series),('temperature',temps),('e_delta',fields),*quantities.items()]:
            if not seq:continue
            item=seq[i] if len(seq)>1 else seq[0]
            values[key]=item['value'];src[key]=_source(loc,item['span'],item['value'])
            if key=='formula':values['composition']=item['value'];src['composition']=src[key]
            elif 'unit' in item:
                uk={'temperature':'temperature_unit','e_delta':'field_unit'}.get(key,key+'_unit')
                values[uk]=item['unit'];src[uk]=_source(loc,item['unit_span'],item['unit'])
        row=draft_row(values,loc,doi,warnings)
        row['field_sources'].update(src)
        row['pairing']={'status':'EXPLICIT','rule':'ordered_respectively' if multi else 'single_local_condition',
                        'index':i,'count':n,'context':dict(loc)}
        row['identity_scope']=identity_text
        out.append(row)
    return out


def extract_pdf(content:bytes,filename:str)->PaperDraft:
    if len(content)>MAX_BYTES:raise ValueError('单篇 PDF 最大 20 MB')
    if Path(filename).suffix.lower()!='.pdf' or not content.startswith(b'%PDF-'):raise ValueError('请上传有效 PDF 文件')
    try:
        import pdfplumber
    except ImportError as exc:
        raise ValueError('离线 PDF 解析组件未安装，请安装当前开发版依赖（pdfplumber）后重试') from exc
    try:
        pdf=pdfplumber.open(BytesIO(content))
    except Exception as exc:raise ValueError('PDF 无法解析，可能已加密或损坏') from exc
    with pdf:
        if not 0<len(pdf.pages)<=MAX_PAGES:raise ValueError('首版每篇限 1–100 页')
        pages=[p.dedupe_chars(tolerance=1,extra_attrs=()) for p in pdf.pages]
        texts=[p.extract_text(x_tolerance=1,y_tolerance=3) or '' for p in pages]
        first=norm(texts[0]); metadata=pdf.metadata or {}
        dm=re.search(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+',str(metadata.get('DOI',''))+' '+first)
        doi=dm.group().rstrip('.,;)') if dm else None
        copyright_lines=[l for t in texts for l in t.splitlines() if re.search(r'copyright|©|creative commons|all rights reserved',l,re.I)]
        draft=PaperDraft(Path(filename).name,hashlib.sha256(content).hexdigest(),len(pdf.pages),
            str(metadata.get('Title') or first[:180]),doi,'\n'.join(copyright_lines) or '许可未知；仅供用户本地核对，不授予再分发权。')
        in_references=False
        for page_no,(page,raw_text) in enumerate(zip(pages,texts),1):
            if not raw_text.strip():
                draft.notices.append(f'第 {page_no} 页无可搜索文本，需要 OCR 或人工处理。')
                row=draft_row({},location(page_no,'page','扫描页待 OCR','本页无可搜索文本。'),doi,['需要 OCR 或人工处理。'])
                row['evidence_only']=True;draft.candidates.append(row)
                continue
            before_count=len(draft.candidates)
            rows,table_boxes=_table_rows(page,page_no,doi)
            draft.candidates.extend(rows)
            # Store local searchable page text/table/caption evidence even when rules cannot parse it.
            draft.blocks.append(location(page_no,'page','页面正文',raw_text))
            words=page.extract_words(x_tolerance=1,y_tolerance=3)
            mid=page.width/2
            crossing=sum(w['x0']<mid-6 and w['x1']>mid+6 for w in words)
            # A clear central gutter indicates two columns. Avoid cross-column sentence association.
            regions=[(0,0,mid,page.height),(mid,0,page.width,page.height)] if crossing<4 and len(words)>100 else [(0,0,page.width,page.height)]
            for bbox in regions:
                cropped=page.crop(bbox)
                if table_boxes:
                    cropped=cropped.filter(lambda obj: not any(b[1]<=obj.get('top',-1)<=b[3] for b in table_boxes))
                # Keep geometric paragraphs separate, especially captions and
                # body text that discusses other works immediately underneath.
                for para,para_box in _paragraphs(cropped):
                    if re.search(r'Supplementary (?:Information|Materials|Notes?)',para,re.I):in_references=False
                    if re.match(r'References\b',para.strip()):in_references=True
                    if in_references:continue
                    caption=re.match(r'(?:Fig(?:ure)?\.?\s*\d+)[^\n]*',para.strip(),re.I)
                    if caption and (re.search(r'\bshow|\billustrat|\bdepict|Supplementary',caption.group(),re.I) or not re.search(r'[:|]|^Fig\.\s*\d+\s+[A-Z]',caption.group())):
                        caption=None
                    if caption:
                        # A caption may run into body text. Only its paragraph is treated as the caption.
                        captext=para.split('\n\n')[0]
                        label=re.match(r'(?:Fig(?:ure)?\.?\s*\d+)',caption.group(),re.I).group()
                        loc=location(page_no,'caption',label,norm(captext),para_box)
                        draft.blocks.append(loc)
                        if re.search(r'Δ[TS]|electrocaloric|EC temperature|entropy change',captext,re.I):
                            candidates=scalar_candidates(captext,loc,doi)
                            if not candidates:
                                row=draft_row({},loc,doi,['曲线中的数值尚未数字化；未从坐标或像素推测精确值。'])
                                row['status']='需要图像数字化';draft.candidates.append(row)
                            else:draft.candidates.extend(candidates)
                    else:
                        for sentence in re.split(r'(?<=[.;])\s+(?=[A-Z(])',norm(para)):
                            if not re.search(r'Δ[TS]|temperature (?:change|drop)|entropy change',sentence,re.I):continue
                            loc=location(page_no,'text','正文片段',sentence,para_box)
                            draft.candidates.extend(scalar_candidates(sentence,loc,doi))
            if len(draft.candidates)==before_count and re.search(r'Δ\s*[TS]|temperature change|entropy change',norm(raw_text),re.I) and not in_references:
                row=draft_row({},location(page_no,'page','电卡证据未解析',raw_text),doi,
                    ['本页含电卡物理量，但未建立明确标量或条件配对；需核对原文/表格/图像。'])
                row['evidence_only']=True;draft.candidates.append(row)
            if len(draft.candidates)>500:raise ValueError('候选超过 500 条，请拆分 PDF 后核对')
        from .paper_policy import assess, CITED
        for row in draft.candidates:
            if 'SYNTHETIC_TEST_DATA' in filename.upper() or 'SYNTHETIC_TEST_DATA' in first:
                row['values']['data_kind']='SYNTHETIC_TEST_DATA'
            _locate_fields(row, pages[row['location']['page']-1])
        _resolve_aliases(draft,pages,texts)
        for row in draft.candidates:
            row['missing']=[k for k in CORE if row['values'].get(k) in (None,'','unknown')]
            assess(row,draft)
            if row['attribution']['kind']==CITED:
                row['values'].pop('doi',None)
                row['field_sources'].pop('doi',None)
            if row['status']!='需要图像数字化':
                row['status']={'CITATION':'引用数据','AUTO_ELIGIBLE':'规则预检通过','REVIEW':'待确认'}[row['admission']['route']]
        seen=set(); unique=[]
        for row in draft.candidates:
            identity=json.dumps([row['values'],row['location']],sort_keys=True,ensure_ascii=False)
            cid=hashlib.sha256(identity.encode()).hexdigest()[:20]
            if cid in seen:continue
            seen.add(cid);row['candidate_id']=cid
            if 'SYNTHETIC_TEST_DATA' in filename.upper() or 'SYNTHETIC_TEST_DATA' in first:
                row['values']['data_kind']='SYNTHETIC_TEST_DATA'
            unique.append(row)
        draft.candidates=unique
        draft.notices.append('按归属和配对证据分流；复杂跨句、缩写、补充文件关联及曲线仍可能待确认。自动入库另受独立验收门禁控制。')
        return draft


def prepare_review(content, draft:PaperDraft, reviewed:list[dict], *,confirmed=False,license_note=''):
    """Revalidate edited rows through the CSV pipeline; immutable PDF locators survive edits."""
    if PERSONAL:raise ValueError('论文 MVP 仅在 BYO 本机工作区开放，避免写入原科研数据库。')
    if not confirmed:raise ValueError('请先核对并明确确认实验数据及来源')
    if hashlib.sha256(content).hexdigest()!=draft.sha256:raise ValueError('PDF 已变化，请重新提取')
    if not license_note.strip():raise ValueError('请填写本地使用及版权说明')
    originals={r['candidate_id']:r for r in draft.candidates}
    values=[]; selected=[]; selected_items=[]
    for item in reviewed:
        if not item.get('selected'):continue
        row=originals.get(item['candidate_id'])
        if row is None:raise ValueError('来源候选不存在，请重新提取')
        if row['status']=='需要图像数字化':raise ValueError('需要图像数字化的条目不能作为精确实验记录保存')
        from .paper_policy import attribution, CITED, THEORY, OWN
        ownership=attribution(row)['kind']
        if ownership in {CITED,THEORY}:
            raise ValueError('引用数据或理论/模拟数据不能作为本论文实验记录入库；原始测量 DOI 不得使用当前论文 DOI')
        if ownership!=OWN and not (item.get('ownership_confirmed') and str(item.get('review_note','')).strip()):
            raise ValueError('归属不确定：请明确确认本论文实验归属并填写原文核对依据')
        if row.get('pairing',{}).get('status')!='EXPLICIT' and not str(item.get('review_note','')).strip():
            raise ValueError('配对关系不确定：请填写材料、温度、ΔE 与性能的原文核对依据')
        v={k:val for k,val in item['values'].items() if k in FIELDS and val is not None and str(val).strip()}
        if row['values'].get('data_kind')=='SYNTHETIC_TEST_DATA':v['data_kind']='SYNTHETIC_TEST_DATA'
        if not any(v.get(k) is not None and str(v[k]).strip() for k in ('delta_t','delta_s')):raise ValueError('实验记录至少需要一个已核对 ΔT 或 ΔS；纯待办不写入实验库')
        values.append(v);selected.append(row);selected_items.append(item)
    if not values:raise ValueError('请选择至少一条已核对的实验记录')
    csv=pd.DataFrame(values).fillna('').to_csv(index=False).encode()
    mapping={k:k for k in pd.DataFrame(values).columns}
    prepared=preview(csv,'paper-review.csv',mapping)
    if prepared['errors']:return prepared
    prepared.update(sha256=draft.sha256,filename=draft.filename,rows=len(values),
                    mapping={'extractor':EXTRACTOR_VERSION,'reviewed_fields':list(mapping)},units={},sheet=None)
    for record,row,v,item in zip(prepared['records'],selected,values,selected_items):
        ownership=attribution(row)['kind']
        loc=row['location']; loc_label=f"PDF p.{loc['page']} · {loc['label']}"
        rid=hashlib.sha256(json.dumps([draft.sha256,row['candidate_id'],v],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        record.record_id='paper-'+rid[:28]
        record.source_id='paper-'+draft.sha256
        record.original_row=loc_label
        record.figure_id=loc['label'] if loc['kind']=='caption' else None
        record.raw_metadata_json.update(entry_method='PAPER_PDF_REVIEWED',provenance='USER_REVIEWED_NOT_INDEPENDENTLY_VERIFIED',
            paper={'sha256':draft.sha256,'filename':draft.filename,'title':draft.title,'doi_detected':draft.doi,
                   'copyright':draft.copyright,'license_note':license_note,'local_only':True,'redistribution_allowed':False},
            source_location=loc,field_sources={k:{**row['field_sources'].get(k,loc),'origin':'extracted' if str(v[k])==str(row['values'].get(k)) else 'user_corrected'} for k in v},
            extracted_values=row['values'],reviewed_values=v,extraction_warnings=row['warnings'],
            cited_reference=row['reference'],extractor_version=EXTRACTOR_VERSION,
            paper_candidate_id=row['candidate_id'],
            attribution=attribution(row),pairing=row.get('pairing'),
            ownership_review={'kind':OWN,'reviewer':'user','note':item.get('review_note','')} if ownership!=OWN else None,
            pairing_review_note=item.get('review_note',''))
        record.quality_flags=sorted(set(record.quality_flags+['paper_extraction_user_reviewed','user_supplied_unverified']))
        record.validate()
    identity=json.dumps([draft.sha256,EXTRACTOR_VERSION,[r.record_id for r in prepared['records']]],sort_keys=True)
    prepared['import_id']=hashlib.sha256(identity.encode()).hexdigest()
    prepared['preview']=[r.to_dict() for r in prepared['records']]
    prepared['sources']={prepared['records'][0].source_id:{'filename':draft.filename,'doi':draft.doi,'title':draft.title,
          'copyright':draft.copyright,'license_note':license_note,'sha256':draft.sha256,'local_only':True,'redistribution_allowed':False}}
    return prepared


def save_review(content,draft,reviewed,workspace,*,confirmed=False,license_note=''):
    prepared=prepare_review(content,draft,reviewed,confirmed=confirmed,license_note=license_note)
    return commit(content,prepared,workspace)


def retain_local_draft(content, draft, workspace):
    """Retain original PDF plus unresolved/digitization work without writing evidence."""
    if PERSONAL:raise ValueError('论文 MVP 仅限 BYO 工作区')
    if hashlib.sha256(content).hexdigest()!=draft.sha256:raise ValueError('PDF 校验失败')
    import os
    root=Path(workspace)
    raw=root/'raw'; drafts=root/'paper_drafts'
    for directory in (raw,drafts):directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    payload=json.dumps({'extractor_version':EXTRACTOR_VERSION,'local_only':True,
        'redistribution_allowed':False,**asdict(draft)},ensure_ascii=False,indent=2).encode()
    revision=hashlib.sha256(payload).hexdigest()[:16]
    for target,data in [(raw/(draft.sha256+'.pdf'),content),(drafts/(draft.sha256+'-'+revision+'.json'),payload)]:
        if target.exists():
            if target.read_bytes()!=data:raise ValueError('本地原件校验失败，拒绝覆盖')
        else:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as handle:handle.write(data)
    return {'raw_file':str(raw/(draft.sha256+'.pdf')),'draft_file':str(drafts/(draft.sha256+'-'+revision+'.json'))}
