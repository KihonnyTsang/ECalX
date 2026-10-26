from pathlib import Path
import json
import pytest
from streamlit.testing.v1 import AppTest
from ecalx.agent.contracts import Goals
from ecalx.data.repository import EvidenceRepository
from ecalx.services.discovery import comparable_key, unmet_constraints
from ecalx_local import exploratory
from ecalx_local.import_data import FIELDS, preview, commit
from ecalx_local.inputs import prepare_inputs
from ecalx_local.field_protocol import (
    UNKNOWN, PULSE, CUSTOM, reference_protocol, imported_protocol, protocol_lines,
)
from ecalx_local.jobs import create_research
from ecalx_local.engine import LocalAgent, LocalWorkflow
from ecalx_local.presentation import report_markdown

ROOT = Path(__file__).parents[1]
CSV = ROOT/'examples/SYNTHETIC_TEST_DATA_missing_endpoints.csv'


def prepared_csv():
    content = CSV.read_bytes()
    columns = content.decode().splitlines()[0].split(',')
    p = preview(content, CSV.name, {k:k for k in FIELDS if k in columns})
    assert p['errors'] == []
    return content, p


def test_missing_endpoints_immutable_and_methods_separate(isolation):
    content, p = prepared_csv()
    commit(content, p, isolation)
    repo = EvidenceRepository(isolation/'data/evidence.sqlite3')
    before = [r.to_dict() for r in repo.records()]
    references = [reference_protocol(PULSE, peak=10), reference_protocol(CUSTOM, start=50, end=60, delta=10)]
    for record in repo.records():
        for ref in references:
            exploratory.estimate(formula=record.formula_normalized, protocol=ref)
    assert [r.to_dict() for r in repo.records()] == before
    assert next((isolation/'raw').iterdir()).read_bytes() == content
    assert CSV.read_bytes() == content
    assert all(r.e_start_kV_cm is None and r.e_end_kV_cm is None for r in p['records'][:4])
    assert p['records'][0].e_delta_kV_cm is None
    assert imported_protocol(p['records'][0])['peak_kV_cm'] is None
    assert imported_protocol(p['records'][1])['peak_kV_cm'] is None
    assert imported_protocol(p['records'][2])['peak_kV_cm'] == 10
    assert p['records'][2].raw_metadata_json['e_peak'] == '10'
    assert imported_protocol(p['records'][4])['status'] == 'UNKNOWN'
    assert comparable_key(p['records'][2]) != comparable_key(p['records'][3])
    assert [r.measurement_method for r in p['records'][2:4]] == ['direct','indirect']
    request = Goals(e_start_kV_cm=0, e_end_kV_cm=10).request()
    assert all(unmet_constraints(r,request) for r in p['records'][:4])
    assert not unmet_constraints(p['records'][4],request)


def test_pulse_reference_and_unknown_values():
    p = prepare_inputs({'field_protocol':PULSE,'e_peak':'1','field_unit':'kV/mm',
                        'field_kind':'上限','field_value':'2'})
    ref = p['field_protocol']
    assert ref['reference_path_kV_cm'] == [0,10,10,0]
    assert ref['status'] == 'ASSUMED' and not ref['experimentally_verified']
    assert all(ref[k] is None for k in ('hold_seconds','rise_rate_kV_cm_s','fall_rate_kV_cm_s','measurement_branch'))
    assert p['goals']['e_start_kV_cm'] == 0 and p['goals']['e_end_kV_cm'] == 10
    assert p['goals']['e_max_kV_cm'] == 20 and p['goals']['delta_E_kV_cm'] is None
    assert 'ASSUMED' in '\n'.join(p['summary'])


@pytest.mark.parametrize('kind',['上限','变化幅度 ΔE'])
def test_scalar_never_becomes_peak_or_endpoints(kind):
    p = prepare_inputs({'field_protocol':UNKNOWN,'field_kind':kind,'field_value':'10','field_unit':'kV/cm'})
    assert p['field_protocol']['status'] == 'UNKNOWN'
    assert p['field_protocol']['peak_kV_cm'] is None
    assert p['goals']['e_start_kV_cm'] is None and p['goals']['e_end_kV_cm'] is None
    with pytest.raises(ValueError, match='单独填写'):
        prepare_inputs({'field_protocol':PULSE,'field_kind':kind,'field_value':'10'})


def test_custom_bias_and_delta_validation():
    p = prepare_inputs({'field_protocol':CUSTOM,'field_kind':'上限','field_value':'70',
                        'e_start':'50','e_end':'60','e_delta':'10'})
    assert p['field_protocol']['start_kV_cm'] == 50
    assert p['field_protocol']['delta_kV_cm'] == 10
    assert p['field_protocol']['reference_path_kV_cm'] is None
    with pytest.raises(ValueError):
        prepare_inputs({'field_protocol':CUSTOM,'e_start':'50','e_end':'60','e_delta':'20'})
    with pytest.raises(ValueError,match='协议未知'):
        prepare_inputs({'field_protocol':UNKNOWN,'e_start':'0','e_end':'10','field_kind':'起点 → 终点'})


@pytest.mark.parametrize('model',['RandomForest','XGBoost'])
@pytest.mark.parametrize('method',['direct','indirect'])
@pytest.mark.parametrize('origin',['current','parent','tampered_unknown','tampered_observed'])
def test_models_cannot_disguise_assumptions_as_unknown(monkeypatch,model,method,origin):
    monkeypatch.setattr(exploratory,'PERSONAL',True)
    def forbidden(*args,**kwargs):
        pytest.fail('unsupported path must be refused before loading the model')
    monkeypatch.setattr(exploratory,'model_bundle',forbidden)
    ref = reference_protocol(PULSE,peak=10)
    if origin == 'tampered_unknown':
        ref['status'] = 'UNKNOWN'
    if origin == 'tampered_observed':
        ref['status'] = 'OBSERVED'
        ref['experimentally_verified'] = True
    result = exploratory.estimate(formula='BaTi0.89Sn0.11O3',temperature_K=343,
        delta_E_kV_cm=10,method=method,model=model,sample_form='bulk ceramic',
        field_path='unknown',**({'parent_protocol':ref} if origin=='parent' else {'protocol':ref}))
    assert result['status'] == 'REJECT'
    assert result['reasons'] == ['path_specific_prediction_not_supported']
    assert result['prediction_K'] is None and not result['experimentally_verified']
    assert result['formal_delta_T'] == 'unknown'
    if origin != 'parent':
        assert result['field_protocol']['status'] == 'ASSUMED'
        assert not result['field_protocol']['experimentally_verified']


def test_new_temperature_draft_keeps_reference_without_changing_parent():
    from ecalx_local.conversation import prepare_child
    prepared = prepare_inputs({'field_protocol':PULSE,'e_peak':'10'})
    task = {'id':'task-'+'a'*32,'frozen_goals':prepared['goals'],'input_snapshot':prepared}
    before = json.dumps(task,sort_keys=True)
    child = prepare_child(task,'修改温区',{**task['frozen_goals'],'temperature_min_K':300})
    assert child['field_protocol']['status'] == 'ASSUMED'
    assert child['field_protocol']['kind'] == 'standard_pulse'
    assert json.dumps(task,sort_keys=True) == before
    changed = prepare_child(task,'修改场路径',{**task['frozen_goals'],'e_start_kV_cm':50,'e_end_kV_cm':60})
    assert changed['field_protocol']['kind'] == 'custom'
    assert changed['field_protocol']['start_kV_cm'] == 50


def test_import_peak_conversion_and_bounds():
    p = preview(b'formula,e_peak,e_max\nBaTiO3,1,2\n','peak.csv',
                {'formula':'formula','e_peak':'e_peak','e_max':'e_max'},units={'field_unit':'kV/mm'})
    assert not p['errors']
    r = p['records'][0]
    assert imported_protocol(r)['peak_kV_cm'] == 10
    assert r.e_max_kV_cm == 20 and r.e_start_kV_cm is None and r.e_end_kV_cm is None
    bad = preview(b'formula,e_peak,e_max\nBaTiO3,3,2\n','bad.csv',
                  {'formula':'formula','e_peak':'e_peak','e_max':'e_max'},units={'field_unit':'kV/mm'})
    assert bad['errors']


def test_local_task_receipts_preserve_reference_and_snapshot(isolation,tmp_path):
    content,p = prepared_csv()
    commit(content,p,isolation)
    prepared = prepare_inputs({'natural_language':'探索材料候选','field_protocol':PULSE,'e_peak':'10'})
    task = create_research(prepared,'local',confirmed=True,storage=tmp_path/'tasks')
    agent = LocalAgent(tmp_path/'tasks')
    agent.advance(task['id'],LocalWorkflow())
    agent.confirm_plan(task['id'],confirmed=True)
    done = agent.run_until_wait(task['id'],LocalWorkflow())
    assert done['status'] == 'COMPLETED', done.get('error')
    summary = next(c for c in done['calls'] if c['name']=='search_material_evidence')['output']['data']['condition_evidence']
    assert summary['field_reference']['status'] == 'ASSUMED'
    assert summary['field_matches'] == 1  # only supplied endpoints, no assumed rows
    assert summary['full_reference_protocol_matches'] is None
    assert summary['field_match_scope'] == 'reference_loading_segment_only'
    assert 'ASSUMED' in report_markdown(done)
    from ecalx_local.conversation import fact_catalog
    assert 'ASSUMED' in fact_catalog(done)['overview']['text']
    frozen = json.dumps(agent.get(task['id']),sort_keys=True)
    exploratory.estimate(protocol=done['input_snapshot']['field_protocol'])
    assert json.dumps(agent.get(task['id']),sort_keys=True) == frozen
    assert all(r.e_start_kV_cm is None for r in EvidenceRepository(done['evidence_snapshot']).records()[:4])
    assert not (isolation/'api_usage.sqlite3').exists()


def test_online_context_only_projects_host_reference_no_requests(isolation,tmp_path):
    from ecalx_local.jobs import LiveIdentity
    agent = LocalAgent(tmp_path/'tasks')
    task = agent.create_task('研究参考脉冲',LiveIdentity())
    p = prepare_inputs({'field_protocol':PULSE,'e_peak':'10'})
    agent.establish_goals(task['id'],p['goals'],confirmed=True)
    task = agent.get(task['id'])
    task['input_snapshot'] = p
    header = json.loads(agent.context(task)[0]['content'])
    assert header['field_reference']['status'] == 'ASSUMED'
    assert header['field_reference']['measurement_branch'] is None
    assert not header['field_reference']['experimentally_verified']
    assert task['llm_requests'] == 0


def test_ui_pulse_review_and_edit(isolation,tmp_path):
    at = AppTest.from_file(str(ROOT/'app.py')).run(timeout=20)
    assert not at.exception
    assert at.selectbox(key='home_protocol').value == UNKNOWN
    at.selectbox(key='home_protocol').set_value(PULSE)
    at.text_input(key='home_peak').set_value('10')
    next(b for b in at.button if b.label=='开始研究').click().run()
    assert not at.exception
    assert any('ASSUMED' in m.value for m in at.markdown)
    assert at.session_state['prepared']['field_protocol']['reference_path_kV_cm'] == [0,10,10,0]
    next(b for b in at.button if b.label=='返回修改研究条件').click().run()
    assert not at.exception and at.text_input(key='home_peak').value == '10'


def test_ui_import_reference_opt_in_preserves_originals(isolation):
    content,p = prepared_csv()
    commit(content,p,isolation)
    before = [r.to_dict() for r in EvidenceRepository(isolation/'data/evidence.sqlite3').records()]
    at = AppTest.from_file(str(ROOT/'app.py')).run(timeout=20)
    next(b for b in at.button if b.label=='设置').click().run()
    next(b for b in at.button if b.label=='数据库构建').click().run()
    ref = next(s for s in at.selectbox if s.label=='电场协议')
    ref.set_value(PULSE).run()
    assert not at.exception
    assume = next(c for c in at.checkbox if '假设采用标准脉冲协议' in c.label)
    assert assume.value is False
    assume.check()
    next(t for t in at.text_input if t.label=='参考峰值电场 / kV/cm').set_value('10')
    next(b for b in at.button if b.label=='检查参考协议与预测适用性').click().run()
    assert not at.exception
    assert any('ASSUMED' in m.value for m in at.markdown)
    assert any('暂不提供性能预测' in m.value for m in at.markdown)
    assert [r.to_dict() for r in EvidenceRepository(isolation/'data/evidence.sqlite3').records()] == before
