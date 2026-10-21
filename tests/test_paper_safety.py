"""Fabricated adversarial cases, distinct from the held-out paper evaluation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from ecalx_local.paper_extract import scalar_candidates, location, extract_pdf, prepare_review
from ecalx_local.paper_policy import assess, OWN, CITED, UNCERTAIN, THEORY
from ecalx_local.paper_batch import process_batch, summarize, import_automatic, revoke_paper_import
from ecalx.data.repository import EvidenceRepository
from test_paper_extract import pdf_bytes, selection


def parse(text):
    rows=scalar_candidates(text,location(2,'text','Results',text,[0,0,600,800]),'10.1234/test')
    return [assess(r) for r in rows]


@pytest.mark.parametrize('prefix,expected',[
    ('Previously reported by Smith et al. [12], ',CITED),
    ('In the Results comparison, reported by Smith [4], ',CITED),
    ('In this work we measured ',OWN),
    ('In the Introduction, in this work we measured ',OWN),
    ('In this work, compared with Smith et al. [4], ',UNCERTAIN),
    ('In the Results section, ',UNCERTAIN),
    ('In this work using a first-principles simulation, ',THEORY),
])
def test_attribution_uses_claim_not_heading(prefix,expected):
    row=parse(prefix+'BaTiO3 ceramic, direct, ΔT = 1.2 K at 300 K, ΔE = 10 kV/cm.')[0]
    assert row['attribution']['kind']==expected
    if expected!=OWN:assert row['admission']['route']!='AUTO_ELIGIBLE'


@pytest.mark.parametrize('text,expected',[
 ('In this work BaTiO3 ceramic, direct at 300 K and 310 K, ΔE = 10 kV/cm, ΔT = 1.2 K and 1.4 K respectively.',
  [('BaTiO3','300','10','1.2'),('BaTiO3','310','10','1.4')]),
 ('In this work BaTiO3 ceramic, direct at 300 K, ΔE = 10 and 20 kV/cm, ΔT = 1.2 and 1.4 K respectively.',
  [('BaTiO3','300','10','1.2'),('BaTiO3','300','20','1.4')]),
 ('In this work BaTiO3 and SrTiO3 ceramic, direct at 300 and 310 K, ΔE = 10 and 20 kV/cm, ΔT = 1.2 and 1.4 K respectively.',
  [('BaTiO3','300','10','1.2'),('SrTiO3','310','20','1.4')]),
 ('In this work BaTiO3 ceramic, direct at 300, 310 and 320 K, ΔE = 10 kV/cm, ΔT = 1.2, 1.4 and 1.6 K respectively.',
  [('BaTiO3','300','10','1.2'),('BaTiO3','310','10','1.4'),('BaTiO3','320','10','1.6')]),
])
def test_exact_ordered_pairing(text,expected):
    rows=parse(text)
    assert [(r['values'].get('formula'),r['values'].get('temperature'),r['values'].get('e_delta'),r['values'].get('delta_t')) for r in rows]==expected
    assert all(r['pairing']['status']=='EXPLICIT' for r in rows)


@pytest.mark.parametrize('text',[
 'BaTiO3 and SrTiO3, ΔT = 1.2 and 1.4 K at 300 and 310 K, ΔE = 10 and 20 kV/cm.',
 'BaTiO3 and SrTiO3, ΔT = 1.2, 1.4 and 1.6 K respectively, at 300 K.',
 'BaTiO3, ΔT = 1.2 and 1.4 K, ΔS = 2 J/kg/K respectively at 300 and 310 K.',
])
def test_ambiguous_vectors_produce_no_labels(text):
    rows=parse(text)
    assert len(rows)==1 and rows[0]['pairing']['status']=='UNRESOLVED'
    assert not any(rows[0]['values'].get(k) for k in ('delta_t','delta_s'))
    assert rows[0]['location']['quote']==text


def fixture():
    # Deliberately constructed source, used only in isolated tests.
    return pdf_bytes(['Original synthetic software fixture 10.1234/fixture',
      'We measured BaTiO3 ceramic by direct method: adiabatic temperature change 1.2 K at 300 K, field change 10 kV/cm.'],font_size=9)


def test_gate_locator_delta_only_and_reversible_import(isolation,monkeypatch):
    content=fixture();jobs=process_batch([('software-fixture.pdf',content)],isolation)
    row=jobs[0]['draft'].candidates[0]
    assert row['admission']['route']=='AUTO_ELIGIBLE',row
    assert all(row['field_sources'][k]['bbox'] and row['field_sources'][k]['span'] for k in ('formula','temperature','e_delta','delta_t','method'))
    assert row['field_sources']['temperature']['bbox']!=row['field_sources']['delta_t']['bbox']
    repo=EvidenceRepository(isolation/'data/evidence.sqlite3')
    assert not repo.records()
    with pytest.raises(ValueError,match='独立验收'):import_automatic(jobs,isolation,license_note='Synthetic test')
    # Exercise disabled production branch on synthetic evidence only.
    monkeypatch.setattr('ecalx_local.paper_policy.AUTO_IMPORT_ENABLED',True)
    logs=import_automatic(jobs,isolation,license_note='Synthetic test')
    record=repo.records()[0]
    assert record.e_delta_kV_cm==10 and record.e_start_kV_cm is record.e_end_kV_cm is None
    assert logs[0]['data_version']==repo.data_version
    assert import_automatic(jobs,isolation,license_note='Synthetic test')[0]['already_imported']
    assert summarize(jobs,isolation)['duplicate_records']==1
    result=revoke_paper_import(isolation,logs[0]['import_id'],'Fabricated test rollback')
    assert not repo.records() and result['revocation']['after_version']==repo.data_version
    assert revoke_paper_import(isolation,logs[0]['import_id'],'retry')['revoked']
    assert import_automatic(jobs,isolation,license_note='Synthetic test')[0]['revoked']
    assert not repo.records()


def test_auto_reparses_and_rejects_mutated_ui(isolation,monkeypatch):
    content=fixture();jobs=process_batch([('fixture.pdf',content)],isolation)
    jobs[0]['draft'].candidates[0]['values']['delta_t']='999'
    monkeypatch.setattr('ecalx_local.paper_policy.AUTO_IMPORT_ENABLED',True)
    import_automatic(jobs,isolation,license_note='Synthetic test')
    assert EvidenceRepository(isolation/'data/evidence.sqlite3').records()[0].delta_T_ad_K==1.2


def test_citation_is_a_source_never_current_doi_measurement(isolation):
    content=pdf_bytes(['10.1234/fixture',
        'Smith et al. [12] reported BaTiO3 temperature change of 2 K at 300 K, field change of 10 kV/cm.'])
    jobs=process_batch([('cited-fixture.pdf',content),('duplicate.pdf',content)],isolation)
    row=jobs[0]['draft'].candidates[0]
    assert row['attribution']['kind']==CITED and 'doi' not in row['values']
    assert summarize(jobs,isolation)['cited_records']==1 and summarize(jobs,isolation)['duplicate_papers']==1
    repo=EvidenceRepository(isolation/'data/evidence.sqlite3')
    assert not repo.records()
    with repo._connect() as db:source=json.loads(db.execute('SELECT payload FROM sources').fetchone()[0])
    assert source['enclosing_paper_doi']=='10.1234/fixture' and source['original_measurement_doi'] is None
    with pytest.raises(ValueError,match='引用数据'):prepare_review(content,jobs[0]['draft'],selection(jobs[0]['draft']),confirmed=True,license_note='Synthetic')


def test_uncertain_needs_explicit_user_ownership_and_evidence():
    content=pdf_bytes(['10.1234/fixture','BaTiO3 temperature change of 1.2 K at 300 K.'])
    draft=extract_pdf(content,'fixture.pdf');items=selection(draft)
    with pytest.raises(ValueError,match='归属不确定'):prepare_review(content,draft,items,confirmed=True,license_note='Synthetic')
    items[0].update(ownership_confirmed=True,review_note='Synthetic fixture manually checked')
    prepared=prepare_review(content,draft,items,confirmed=True,license_note='Synthetic')
    assert prepared['records'][0].raw_metadata_json['ownership_review']['kind']==OWN


def test_revocation_refuses_later_edits(isolation,monkeypatch):
    jobs=process_batch([('fixture.pdf',fixture())],isolation)
    monkeypatch.setattr('ecalx_local.paper_policy.AUTO_IMPORT_ENABLED',True)
    log=import_automatic(jobs,isolation,license_note='Synthetic')[0]
    repo=EvidenceRepository(isolation/'data/evidence.sqlite3')
    with repo._connect() as db:
        rid,payload=db.execute('SELECT record_id,payload FROM records').fetchone()
        value=json.loads(payload);value['delta_T_ad_K']=4
        db.execute('UPDATE records SET payload=? WHERE record_id=?',(json.dumps(value),rid))
    with pytest.raises(ValueError,match='后续修改'):revoke_paper_import(isolation,log['import_id'],'test')
    assert repo.records()[0].delta_T_ad_K==4


def test_miller_direction_is_not_a_reference_but_explicit_ref_is():
    row=parse('We measured BaTiO3 crystal oriented along [001], direct method, ΔT = 1.2 K at 300 K, ΔE = 10 kV/cm.')[0]
    assert row['attribution']['kind']==OWN
    row=parse('BaTiO3 crystal [111] in Ref. 111, ΔT = 1.2 K.')[0]
    assert row['attribution']['kind']==CITED


def test_cited_experiment_with_simulation_comparison_remains_cited():
    row=parse('Bai et al. reported ΔT = 1.4 K, in agreement with our simulations.')[0]
    assert row['attribution']['kind']==CITED


def test_micrometre_field_unit_is_only_explicit_delta():
    row=parse('We measured BaTiO3 by direct method at 300 K, ΔT = 1.2 K, ΔE = 2 V µm-1.')[0]
    assert row['values']['e_delta']=='2' and row['values']['field_unit']=='kV/mm'
    row=parse('We measured BaTiO3 at 300 K, ΔT = 1.2 K under 2 V µm-1.')[0]
    assert 'e_delta' not in row['values']


def test_explicit_unique_alias_and_modified_sample_are_distinct():
    content=pdf_bytes(['BaTiO3 (BTO).', 'We measured BTO temperature change 1.2 K at 300 K, field change 10 kV/cm.'])
    row=extract_pdf(content,'fixture.pdf').candidates[0]
    assert row['values']['formula']=='BaTiO3'
    assert row['field_sources']['formula']['origin']=='explicit_alias_definition'
    content=pdf_bytes(['BaTiO3 (BTO).', 'We measured BTO-Mg temperature change 1.2 K at 300 K, field change 10 kV/cm.'])
    assert not extract_pdf(content,'fixture.pdf').candidates[0]['values'].get('formula')


def test_introduction_title_does_not_turn_literature_compilation_into_own_data():
    row=parse('In this work we compile BaTiO3, direct comparison, ΔT = 1.2 K at 300 K, ΔE = 10 kV/cm.')[0]
    assert row['attribution']['kind']==UNCERTAIN
    assert row['admission']['route']=='REVIEW'


def test_evidence_only_pages_are_not_duplicate_measurements():
    from ecalx_local.paper_extract import PaperDraft, draft_row
    r=draft_row({},location(1,'page','unresolved','EC evidence page one'),None)
    r['evidence_only']=True;assess(r);r['candidate_id']='one'
    s=deepcopy(r);s['location']['page']=2;s['candidate_id']='two'
    d=PaperDraft('fixture.pdf','a'*64,2,'Synthetic test',None,'Synthetic',candidates=[r,s])
    report=summarize([{'draft':d}])
    assert report['duplicate_records']==0 and report['needs_review']==2


def test_history_import_cannot_be_revoked_as_paper(isolation):
    from ecalx_local.import_data import preview, commit
    content=b'formula,delta_t,delta_t_unit\nBaTiO3,1.2,K\n'
    p=preview(content,'history.csv',{'formula':'formula','delta_t':'delta_t','delta_t_unit':'delta_t_unit'})
    log=commit(content,p,isolation)
    repo=EvidenceRepository(isolation/'data/evidence.sqlite3');before=repo.data_version
    with pytest.raises(ValueError,match='历史科研数据'):revoke_paper_import(isolation,log['import_id'],'must refuse')
    assert repo.data_version==before and len(repo.records())==1


def test_switching_queues_resets_source_locator(isolation,monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    content=pdf_bytes(['10.1234/fixture',
        'We measured BaTiO3 temperature change 1.1 K at 300 K.',
        'We measured SrTiO3 temperature change 1.2 K at 310 K.',
        'Smith et al. [4] reported BaTiO3 temperature change 2.1 K.',
        'Jones et al. [5] reported SrTiO3 temperature change 2.2 K.'],font_size=9)
    class Uploaded:
        name='fixture.pdf'
        def getvalue(self):return content
    original=st.file_uploader
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:[Uploaded()] if kw.get('key')=='paper_upload' else original(*a,**kw))
    at=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=20)
    def click(label):next(b for b in at.button if b.label==label).click().run()
    click('设置');click('数据库构建');click('提取论文数据')
    at.selectbox(key='paper_locator_1_0_待确认').select(1).run()
    assert any('1.2 K' in c.value for c in at.code)
    at.selectbox(key='paper_queue').select('引用数据').run()
    assert at.selectbox(key='paper_locator_1_0_引用数据').value==0
    assert any('2.1 K' in c.value for c in at.code)
    assert not EvidenceRepository(isolation/'data/evidence.sqlite3').records()
