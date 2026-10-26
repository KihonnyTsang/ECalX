"""Offline scientific-boundary and storage tests; all PDFs here are fabricated."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from ecalx_local.paper_extract import (
    PaperDraft, extract_pdf, scalar_candidates, location, prepare_review, save_review,
)
from ecalx.data.repository import EvidenceRepository


def pdf_bytes(lines, font_size=11):
    """Small searchable Type1 test PDF, original synthetic software fixture."""
    def literal(t):return t.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
    stream=f'BT /F1 {font_size} Tf 45 750 Td '+ ' '.join(('0 -20 Td ' if i else '')+'('+literal(t)+') Tj' for i,t in enumerate(lines))+' ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length '+str(len(stream.encode())).encode()+b' >>\nstream\n'+stream.encode()+b'\nendstream']
    out=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(out));out+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(out);out+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    out+=b''.join(f'{n:010} 00000 n \n'.encode() for n in offsets[1:])
    return out+f'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


def rows(text):return scalar_candidates(text,location(2,'text','正文片段',text),'10.1234/test')


def synthetic_draft():
    content=pdf_bytes(['SYNTHETIC_TEST_DATA - not research evidence',
        'In this work we measured BaTiO3 direct at 298 K, temperature change of 1.2 K, field change 10 kV/cm.'])
    draft=extract_pdf(content,'SYNTHETIC_TEST_DATA.pdf')
    return content,draft


def selection(draft):
    return [{'selected':True,'candidate_id':r['candidate_id'],'values':r['values']} for r in draft.candidates if r['values'].get('delta_t')]


def test_searchable_pdf_and_explicit_delta_only(isolation):
    content,draft=synthetic_draft()
    assert draft.pages==1 and len(draft.candidates)==1
    assert not (isolation/'data/evidence.sqlite3').exists()
    r=draft.candidates[0]
    assert r['values']['formula']=='BaTiO3' and r['values']['method']=='direct'
    assert r['values']['e_delta']=='10' and r['values']['temperature']=='298'
    assert r['location']['page']==1 and r['field_sources']['delta_t']['quote']
    selected=selection(draft)
    # Preserve the synthetic marker while exercising the real save pipeline.
    selected[0]['values']['data_kind']='SYNTHETIC_TEST_DATA'
    prepared=prepare_review(content,draft,selected,confirmed=True,license_note='Original synthetic test')
    assert not prepared['errors']
    record=prepared['records'][0]
    assert record.e_delta_kV_cm==10 and record.e_start_kV_cm is record.e_end_kV_cm is None
    assert record.delta_T_ad_K==1.2 and not record.recommendable
    log=save_review(content,draft,selected,isolation,confirmed=True,license_note='Original synthetic test')
    repo=EvidenceRepository(isolation/'data/evidence.sqlite3')
    assert len(repo.records())==1 and log['data_version']==repo.data_version
    assert (isolation/log['raw_file']).read_bytes()==content
    assert repo.records()[0].raw_metadata_json['paper']['redistribution_allowed'] is False
    assert save_review(content,draft,selected,isolation,confirmed=True,license_note='Original synthetic test')['already_imported']
    with repo._connect() as db:assert db.execute('SELECT count(*) FROM sources').fetchone()[0]==1


def test_separate_conditions_methods_signs_and_peak():
    r=rows('BaTiO3 direct at 300 K, ΔE = 10 kV/cm, ΔT = -1.2 K, ΔS = -0.3 J/kg/K.')[0]
    assert r['values']['delta_t']=='-1.2' and r['values']['delta_s']=='-0.3'
    assert r['values']['delta_t_basis']=='signed_delta_t'
    r=rows('BaTiO3 indirect at 300 K, cooling magnitude 1.2 K under 10 kV/cm.')[0]
    assert r['values']['delta_t']=='1.2' and r['values']['delta_t_basis']=='positive_drop_magnitude'
    assert r['values']['method']=='indirect' and 'e_delta' not in r['values'] and r['values']['e_peak']=='10'
    paired=rows('BaTiO3 direct at 300 K and 310 K, ΔT were 1.3 K and 1.8 K respectively.')
    assert len(paired)==2 and [r['values']['temperature'] for r in paired]==['300','310']
    assert [r['values']['delta_t'] for r in paired]==['1.3','1.8']
    ambiguous=rows('BaTiO3 and SrTiO3 at 300 K and 310 K, ΔT were 1.3 K and 1.8 K.')
    assert not ambiguous[0]['values'].get('delta_t') and not ambiguous[0]['values'].get('formula')
    bound=rows('BaTiO3 ΔT > 1.2 K at 300 K.')[0]
    assert not bound['values'].get('delta_t')


def test_review_confirmation_edits_validation_and_immutable_locator(isolation):
    content,draft=synthetic_draft();items=selection(draft)
    items[0]['values']['data_kind']='SYNTHETIC_TEST_DATA'
    with pytest.raises(ValueError,match='确认'):prepare_review(content,draft,items,license_note='test')
    with pytest.raises(ValueError,match='变化'):prepare_review(content+b'x',draft,items,confirmed=True,license_note='test')
    with pytest.raises(ValueError,match='版权'):prepare_review(content,draft,items,confirmed=True)
    changed=deepcopy(items);changed[0]['values']['delta_t']='bad'
    assert prepare_review(content,draft,changed,confirmed=True,license_note='test')['errors']
    changed[0]['values']['delta_t']='1.3'
    p=prepare_review(content,draft,changed,confirmed=True,license_note='test')
    record=p['records'][0]
    assert record.delta_T_ad_K==1.3
    assert record.raw_metadata_json['extracted_values']['delta_t']=='1.2'
    assert record.raw_metadata_json['field_sources']['delta_t']['origin']=='user_corrected'
    assert record.raw_metadata_json['source_location']==draft.candidates[0]['location']
    assert not (isolation/'data/evidence.sqlite3').exists()


def test_curve_todo_cannot_be_saved(isolation):
    content=pdf_bytes(['SYNTHETIC_TEST_DATA','Fig. 1: Electrocaloric temperature change versus temperature.'])
    draft=extract_pdf(content,'curve.pdf')
    assert draft.candidates[0]['status']=='需要图像数字化'
    item={'selected':True,'candidate_id':draft.candidates[0]['candidate_id'],'values':{'formula':'BaTiO3','delta_t':'1','delta_t_unit':'K'}}
    with pytest.raises(ValueError,match='数字化'):save_review(content,draft,[item],isolation,confirmed=True,license_note='test')
    assert not (isolation/'data/evidence.sqlite3').exists()


def test_invalid_pdf_and_scanned_pages():
    for content in [b'not a pdf',b'%PDF-broken']:
        with pytest.raises(ValueError):extract_pdf(content,'bad.pdf')
    with pytest.raises(ValueError,match='20 MB'):extract_pdf(b'%PDF-'+b'0'*20_000_000,'large.pdf')
    blank=extract_pdf(pdf_bytes([]),'scan.pdf')
    assert blank.candidates[0]['evidence_only'] and any('OCR' in n for n in blank.notices)


def test_database_page_has_third_entry(isolation):
    from streamlit.testing.v1 import AppTest
    at=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=20)
    next(b for b in at.button if b.label=='设置').click().run()
    next(b for b in at.button if b.label=='数据库构建').click().run()
    assert not at.exception
    assert [t.label for t in at.tabs]==['文件上传','实验数据录入','论文数据提取','参考曲线']
    assert next(b for b in at.button if b.label=='提取论文数据').disabled


def test_comparison_specimen_not_assigned_to_measured_point():
    row=rows('A ΔT = 4.0 K was obtained at 80 °C under 352 kV/cm, which is far beyond the transition of BaTiO3 single crystal.')[0]
    assert not row['values'].get('sample_form') and not row['values'].get('formula')
    assert row['values']['delta_t']=='4.0'


def test_local_unresolved_draft_retained_without_evidence_write(isolation):
    from ecalx_local.paper_extract import retain_local_draft
    content,draft=synthetic_draft()
    files=retain_local_draft(content,draft,isolation)
    assert Path(files['raw_file']).read_bytes()==content
    payload=json.loads(Path(files['draft_file']).read_text())
    assert payload['redistribution_allowed'] is False and payload['candidates']
    assert not (isolation/'data/evidence.sqlite3').exists()
    assert retain_local_draft(content,draft,isolation)==files


def test_aligned_table_values_and_literal_quote():
    from ecalx_local.paper_extract import _table_rows
    class Header:
        def extract_text(self):return 'Table 1. Original synthetic table'
    class Page:
        width=600
        def crop(self,bbox):return Header()
        def extract_text(self,**kwargs):return '|ΔT| and |ΔS|; Original synthetic software test table'
        def extract_words(self,**kwargs):
            result=[]
            for top,values in [(60,['Material','T(K)','ΔS(J/kg/K)','Q(kJ/kg)','ΔT(K)','ΔE(kV/cm)','Ref.']),
                (80,['BaTiO3','300','-0.2','0.06','-1.2','10','1']),
                (100,['SrTiO3','310','0.3','0.09','1.4','20','2'])]:
                for i,value in enumerate(values):result.append({'text':value,'x0':20+i*80,'x1':70+i*80,'top':top,'bottom':top+10})
            return result
    rows,_=_table_rows(Page(),2,'10.1234/test')
    assert len(rows)==2
    assert rows[0]['values']['formula']=='BaTiO3'
    assert rows[0]['values']['delta_t']=='-1.2' and rows[0]['values']['delta_s']=='-0.2'
    assert rows[0]['values']['temperature']=='300' and rows[1]['values']['e_delta']=='20'
    assert 'BaTiO3 300 -0.2 0.06 -1.2 10 1' in rows[0]['location']['quote']
    assert rows[0]['reference']=='1' and rows[0]['location']['page']==2


def test_ui_review_edit_stale_save_and_search(isolation,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    content,draft=synthetic_draft()
    class Uploaded:
        name='synthetic-software-fixture.pdf'
        def getvalue(self):return content
    original=st.file_uploader
    def uploader(*args,**kwargs):
        if kwargs.get('key')=='paper_upload':return [Uploaded()]
        return original(*args,**kwargs)
    monkeypatch.setattr(st,'file_uploader',uploader)
    editor=st.data_editor
    desired={'delta_t':None}
    # AppTest has no editable data_editor element; emulate its returned dataframe.
    def edit(*args,**kwargs):
        frame=editor(*args,**kwargs)
        if kwargs.get('key','').startswith('paper_editor_') and desired['delta_t']:
            frame=frame.copy();frame.loc[0,'selected']=True;frame.loc[0,'delta_t']=desired['delta_t']
        return frame
    monkeypatch.setattr(st,'data_editor',edit)
    at=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=20)
    def button(label):return next(b for b in at.button if b.label==label)
    button('设置').click().run();button('数据库构建').click().run()
    button('提取论文数据').click().run()
    assert not at.exception
    assert (isolation/'paper_drafts').exists()
    assert not EvidenceRepository(isolation/'data/evidence.sqlite3').records()
    desired['delta_t']='1.3'
    at.checkbox(key='paper_confirm_1').check().run()
    button('校验所选论文记录').click().run()
    assert not at.exception
    assert any('校验通过' in s.value for s in at.success)
    # Changing a value after validation must invalidate the earlier confirmation.
    desired['delta_t']='1.4'
    at.run()
    assert not any(b.label=='确认保存论文记录到本机数据库' for b in at.button)
    assert not EvidenceRepository(isolation/'data/evidence.sqlite3').records()
    button('校验所选论文记录').click().run()
    button('确认保存论文记录到本机数据库').click().run()
    assert not at.exception
    records=EvidenceRepository(isolation/'data/evidence.sqlite3').records()
    assert len(records)==1 and records[0].delta_T_ad_K==1.4
    assert records[0].raw_metadata_json['extracted_values']['delta_t']=='1.2'
    at.text_input(key='data_search').set_value('BaTiO3').run()
    assert any('匹配 1 条' in x.value for x in at.caption)
    button('确认保存论文记录到本机数据库').click().run()
    assert len(EvidenceRepository(isolation/'data/evidence.sqlite3').records())==1
