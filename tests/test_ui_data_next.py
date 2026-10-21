"""Offline acceptance for frozen titles, draft navigation and shared entry pipeline."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from ecalx.data.repository import EvidenceRepository
from ecalx.agent.contracts import Goals
from ecalx_local.import_data import preview_manual, commit, preview, read_table
from ecalx_local.titles import research_title
from ecalx_local.engine import LocalAgent, LocalWorkflow
from ecalx_local.jobs import create_research
from ecalx_local.inputs import prepare_inputs
from ecalx_local.presentation import report_markdown, evidence_rows

ROOT=Path(__file__).parents[1]
TITLE='有条件材料探索 · 0–40°C · ΔE 100 kV/cm'


def record_values(**extra):
    return dict(formula='BaTiO3',temperature='25',temperature_unit='°C',e_delta='100',
                field_unit='kV/cm',delta_t='1.2',delta_t_unit='K',method='direct',
                source_type='本人实验',source_record_id='LOCAL-TEST-001',**extra)


def test_titles_read_frozen_goals_only():
    task={'original':'','frozen_goals':Goals(temperature_min_K=273.15,temperature_max_K=313.15,delta_E_kV_cm=100).model_dump()}
    before=deepcopy(task)
    assert research_title(task)==TITLE
    assert task==before
    assert research_title({**task,'original':'  自然语言研究需求  '})=='自然语言研究需求'
    assert research_title({'original':' ','frozen_goals':Goals().model_dump()})=='开放探索 · 未指定研究条件'
    assert '电场上限 0' in research_title({'frozen_goals':{'e_max_kV_cm':0}})
    for key,value in [('exclude_elements',['Pb']),('include_elements',['Ba']),('material_type','陶瓷'),('maximize',True),('delta_t_K',1)]:
        assert research_title({'frozen_goals':{key:value}}).startswith('有条件材料探索')


def test_manual_idempotent_persistent_unverified(isolation):
    values=record_values(notes='仅软件验收，不是真实论文数据')
    raw,p=preview_manual(values)
    assert not p['errors']
    r=p['records'][0]
    assert r.temperature_K==298.15 and r.delta_T_ad_K==1.2 and r.e_delta_kV_cm==100
    assert r.e_start_kV_cm is None and r.e_end_kV_cm is None
    assert not r.recommendable and r.doi is None and r.url is None
    assert {'USER_ENTERED','USER_SUPPLIED_UNVERIFIED'} <= set(r.quality_flags)
    log=commit(raw,p,isolation)
    with ThreadPoolExecutor(max_workers=3) as pool:
        repeated=list(pool.map(lambda _:commit(*preview_manual(values),isolation),range(3)))
    assert all(x['already_imported'] for x in repeated)
    repo=EvidenceRepository(isolation/'data/evidence.sqlite3')
    stored=repo.records()
    assert len(stored)==1 and len(repo.logs())==1
    assert stored[0].raw_metadata_json['source_record_id']=='LOCAL-TEST-001'
    assert stored[0].raw_metadata_json['entered_at_utc']==log['timestamp_utc']
    assert log['data_version']==repo.data_version and log['predictions_enabled'] is False


def test_missing_labels_and_peak_not_delta(isolation):
    raw,p=preview_manual({'formula':'SrTiO3','e_delta':'100','field_unit':'kV/cm'})
    commit(raw,p,isolation)
    r=p['records'][0]
    assert r.delta_T_ad_K is None and r.e_start_kV_cm is None and r.e_end_kV_cm is None
    raw,p=preview_manual({'formula':'CaTiO3','e_max':'150','e_peak':'100','field_unit':'kV/cm'})
    assert not p['errors'] and p['records'][0].e_delta_kV_cm is None
    assert p['records'][0].raw_metadata_json['actual_peak_kV_cm']==100


def test_positive_drop_and_methods_stay_separate(isolation):
    for method in ['direct','indirect']:
        values=record_values(delta_t_basis='positive_drop_magnitude',sign_convention='正值表示温降')
        values['method']=method
        raw,p=preview_manual(values)
        assert not p['errors']
        commit(raw,p,isolation)
    records=EvidenceRepository(isolation/'data/evidence.sqlite3').records()
    assert len(records)==2 and {r.measurement_method for r in records}=={'direct','indirect'}
    assert all(r.delta_T_ad_K==1.2 and r.delta_t_basis=='positive_drop_magnitude' for r in records)


@pytest.mark.parametrize('extra',[
    {'formula':'NOT_A_FORMULA'}, {'temperature':'abc'}, {'temperature':'-274'},
    {'field_unit':'wrong'}, {'delta_t_unit':'°F'}, {'delta_s':'1','delta_s_unit':'wrong'},
    {'e_start':'0','e_end':'50'}, {'e_max':'80'}, {'e_peak':'50','e_end':'100'},
    {'e_start':'100','e_end':'0','field_path':'loading'}, {'method':'invalid'},
    {'delta_t_basis':'positive_drop_magnitude','delta_t':'-1'},
    {'delta_t_basis':'positive_drop_magnitude','sign_convention':'signed'},
    {'doi':'bad-doi'}, {'url':'file:///etc/passwd'}, {'source_type':'invalid'},
])
def test_manual_and_csv_share_rejection(isolation,extra):
    values=record_values();values.update(extra)
    raw,p=preview_manual(values)
    bulk=preview(raw,'bulk.csv',p['mapping'])
    assert p['errors'] and p['errors']==bulk['errors']
    with pytest.raises(ValueError):commit(raw,p,isolation)
    assert not (isolation/'data/evidence.sqlite3').exists()


def test_database_upload_refused():
    for name in ['data.sqlite3','data.db','upload.sql']:
        with pytest.raises(ValueError,match='CSV'):
            read_table(b'DROP TABLE records;',name)


def app(tmp_path,monkeypatch):
    import ecalx_local
    monkeypatch.setattr(ecalx_local,'STORAGE',tmp_path/'tasks')
    at=AppTest.from_file(str(ROOT/'app.py')).run(timeout=20)
    assert not at.exception
    return at


def button(at,label):
    return next(b for b in at.button if b.label==label)


def test_navigation_preserves_unsubmitted_and_reviewed_draft(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch)
    at.text_input(key='home_tmin').set_value('0')
    at.text_input(key='home_tmax').set_value('40')
    at.text_input(key='home_edelta').set_value('100')
    button(at,'设置').click().run()
    assert not at.exception
    next(b for b in at.button if b.label=='数据库构建').click().run()
    at.radio(key='main_navigation').set_value('研究与历史').run()
    assert at.text_input(key='home_edelta').value=='100'
    at.radio(key='main_navigation').set_value('历史研究').run()
    at.radio(key='main_navigation').set_value('研究与历史').run()
    assert at.text_input(key='home_tmin').value=='0'
    assert at.text_input(key='home_tmax').value=='40'
    assert at.text_input(key='home_edelta').value=='100'
    button(at,'检查研究条件').click().run()
    frozen=deepcopy(at.session_state['prepared'])
    button(at,'设置').click().run()
    at.radio(key='main_navigation').set_value('研究报告').run()
    at.radio(key='main_navigation').set_value('研究与历史').run()
    assert at.session_state['prepared']==frozen
    assert any(TITLE in x.value for x in at.markdown)


def test_manual_ui_preview_commit_reload_and_stale_edit(isolation,tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch)
    button(at,'设置').click().run()
    next(b for b in at.button if b.label=='数据库构建').click().run()
    for key,value in record_values().items():
        if key in {'temperature_unit','field_unit','delta_t_unit','method','source_type'}:
            at.selectbox(key='manual_'+key).set_value(value)
        else:
            at.text_input(key='manual_'+key).set_value(value)
    button(at,'预览并校验这条记录').click().run()
    assert not at.exception and not (isolation/'raw').exists()
    at.text_input(key='manual_delta_t').set_value('1.3')
    button(at,'确认保存这条记录').click().run()
    assert not (isolation/'raw').exists()
    assert not any(b.label=='确认保存这条记录' for b in at.button)
    button(at,'预览并校验这条记录').click().run()
    button(at,'确认保存这条记录').click().run()
    assert not at.exception
    assert any('已新增 1 条' in x.value for x in at.success)
    button(at,'确认保存这条记录').click().run()
    assert any('重复提交未新增' in x.value for x in at.success)
    fresh=app(tmp_path,monkeypatch)
    button(fresh,'设置').click().run()
    button(fresh,'数据库构建').click().run()
    fresh.text_input(key='data_search').set_value('LOCAL-TEST-001').run()
    assert not fresh.exception
    assert any('匹配 1 条' in c.value for c in fresh.caption)
    assert len(EvidenceRepository(isolation/'data/evidence.sqlite3').records())==1


def test_history_titles_retrieval_and_immutable_snapshots(isolation,tmp_path,monkeypatch):
    raw,p=preview_manual(record_values());commit(raw,p,isolation)
    agent=LocalAgent(tmp_path/'tasks')
    task=create_research(prepare_inputs({'temperature_min':'0','temperature_max':'40','e_delta':'100'}),'local',confirmed=True,storage=agent.storage_root)
    agent.advance(task['id'],LocalWorkflow());agent.confirm_plan(task['id'],confirmed=True)
    done=agent.run_until_wait(task['id'],LocalWorkflow())
    assert done['status']=='COMPLETED'
    assert len(evidence_rows(done))==1 and 'USER_ENTERED' in evidence_rows(done)[0]['quality_flags']
    assert TITLE in report_markdown(done)
    failed=agent.create_task('旧失败研究',LocalWorkflow())
    failed['status']='FAILED';agent.save(failed)
    before={t['id']:json.dumps(t,sort_keys=True) for t in agent.list_tasks()}
    snapshot=Path(done['evidence_snapshot']).read_bytes()
    prior=EvidenceRepository(isolation/'data/evidence.sqlite3').records()[0].to_dict()
    raw,p=preview_manual({'formula':'SrTiO3','e_delta':'100','field_unit':'kV/cm'});commit(raw,p,isolation)
    at=app(tmp_path,monkeypatch)
    at.radio(key='main_navigation').set_value('历史研究').run()
    assert any(TITLE in x.value for x in at.markdown)
    at.text_input[0].set_value('0–40°C').run()
    assert any(TITLE in x.value for x in at.markdown)
    button(at,'打开研究').click().run()
    assert any(TITLE in x.value for x in at.markdown)
    assert not at.exception
    assert {t['id']:json.dumps(t,sort_keys=True) for t in agent.list_tasks()}==before
    assert Path(done['evidence_snapshot']).read_bytes()==snapshot
    assert prior in [r.to_dict() for r in EvidenceRepository(isolation/'data/evidence.sqlite3').records()]
    assert not (isolation/'api_usage.sqlite3').exists()
