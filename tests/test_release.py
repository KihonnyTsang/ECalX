"""Release boundaries, strict contracts, associations and actual Streamlit AppTest."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
import sys

import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest
from ecalx.agent.contracts import Goals, PlanningProposal, ToolOutput, definitions
from ecalx.presentation.candidate_preview import load_candidate
from ecalx.presentation.replay import ReplayError, load_history, load_public, story_view, historical_runs
from helpers import make_packet, write_bundle

ROOT = Path(__file__).resolve().parents[1]


def write_saved_history_fixture(root, task_id):
    """Synthetic saved-state fixture in pytest temp only; never real LLM evidence."""
    folder = root/'reports/m4e411/live/TEST_ONLY_SAVED_STATE'
    folder.mkdir(parents=True)
    (folder/'summary.json').write_text(json.dumps({
        'client_mode':'LIVE_LLM', 'task_id':task_id, 'status':'FAILED'}))
    (folder/'task.json').write_text(json.dumps({
        'client_mode':'LIVE_LLM', 'id':task_id, 'calls':[],
        'original':'TEST_ONLY synthetic saved-state fixture; no LLM or tool execution',
        'frozen_goals':{}, 'plan':{'steps':[]}}))
    (folder/'summary_guard.json').write_text(json.dumps({'passed':False}))
    return folder/'summary.json'


@pytest.mark.parametrize('task_id',['TEST_ONLY_HISTORY_ALPHA','TEST_ONLY_HISTORY_BETA'])
def test_history_limits_and_failed_display_for_arbitrary_ids(tmp_path, monkeypatch, task_id):
    path = write_saved_history_fixture(tmp_path, task_id)
    packet = load_history(tmp_path, path)
    assert packet['summary']['status'] == 'FAILED' and packet['original_guard']['passed'] is False
    monkeypatch.setenv('ECALX_READONLY_ROOT', str(tmp_path))
    app = AppTest.from_file(str(ROOT/'app/readonly_demo.py'), default_timeout=30).run()
    assert not app.exception
    assert any('独立科学审核' in e.value and 'M5正式推荐禁用' in e.value for e in app.caption)
    assert any('ORIGINAL_LIVE_STATUS: FAILED' in e.value and
               'LIVE_END_TO_END_ACCEPTANCE: FAILED' in e.value for e in app.info)
    assert any('原历史运行状态：FAILED' in e.value for e in app.warning)
    states = next(e.value for e in app.dataframe if '判断层' in e.value.columns)
    assert states.loc[states['判断层']=='原自动摘要门禁','状态'].item() == 'FAILED'


@pytest.mark.parametrize('filename,field,value',[
    ('summary.json','client_mode','TEST_ONLY'),
    ('task.json','client_mode','TEST_ONLY'),
    ('summary.json','task_id','TEST_ONLY_MISMATCH'),
])
def test_history_still_rejects_wrong_mode_or_task_binding(tmp_path, filename, field, value):
    path = write_saved_history_fixture(tmp_path, 'TEST_ONLY_HISTORY')
    changed = path.parent/filename
    data = json.loads(changed.read_text()); data[field] = value
    changed.write_text(json.dumps(data))
    with pytest.raises(ReplayError): load_history(tmp_path, path)


def test_real_entry_both_empty_modes(tmp_path, monkeypatch):
    monkeypatch.setenv('ECALX_READONLY_ROOT', str(tmp_path))
    before = list(tmp_path.rglob('*'))
    app = AppTest.from_file(str(ROOT/'app/readonly_demo.py'), default_timeout=30).run()
    assert not app.exception and not app.button and not app.metric
    assert any('尚无真实验证记录' in e.value for e in app.info)
    assert any('本次 API 请求 0' in e.value for e in app.info)
    app.radio(key='replay_mode').set_value('B · 干净环境公开演示').run()
    assert not app.exception and not app.dataframe and not app.button
    assert any('待所有者审核' in e.value for e in app.info)
    assert list(tmp_path.rglob('*')) == before
    assert not any((ROOT/d).exists() for d in ('.git','data','models','reports','demo/review_candidate'))


def test_display_test_double_receipt_associations_ui(tmp_path):
    packet = make_packet()
    folder = tmp_path/'candidate'
    write_bundle(folder, packet)
    loaded = load_candidate(folder)
    view = story_view(loaded)
    assert len(view['details']) == 1 and len(view['timeline']) == 4
    detail = view['details'][0]
    assert detail['receipt']['id'] == 'TEST_ONLY_CALL_2'
    assert not detail['missing_source_ids'] and detail['row']['种子DOI'] == 'UNKNOWN'
    assert detail['row']['性能'] == 'unknown'
    assert detail['receipt']['output']['data']['structure_verified'] is False
    assert all(row['执行状态'] == 'TEST_ONLY_SIMULATED_NOT_EXECUTED' for row in view['timeline'])
    before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    code = ('from pathlib import Path\nfrom ecalx.presentation.candidate_preview import load_candidate\n'
            'from portfolio_demo import render_packet\nrender_packet(load_candidate(Path('+repr(str(folder))+')))')
    app = AppTest.from_string(code, default_timeout=30).run()
    assert not app.exception and not app.button and not app.metric
    app.selectbox[0].set_value(2).run()
    assert not app.exception
    assert any('TEST_ONLY_CANDIDATE' in e.value for e in app.markdown)
    assert any('unknown' in e.value for e in app.warning)
    assert any('TEST_ONLY_SIMULATED_NOT_EXECUTED' in str(e.value) for e in app.dataframe)
    assert before == {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


def test_temporary_approved_test_double_never_becomes_shipped_payload(tmp_path):
    write_bundle(tmp_path/'demo/public', make_packet(), approved_test_double=True)
    assert load_public(tmp_path)['mode'] == 'TEST_ONLY'
    assert load_public(ROOT) is None and historical_runs(ROOT) == []
    assert not (ROOT/'OWNER_APPROVAL.json').exists()


@pytest.mark.parametrize('field,value',[
    ('status','PENDING_INDEPENDENT_REVIEW'), ('owner_approval',''),
    ('owner','TEST_ONLY_WRONG_OWNER'), ('review_record',''),
    ('rights_review','PENDING'), ('privacy_review','PENDING'),
    ('evidence_mode','LIVE_LLM'), ('payload_sha256','0'*64),
])
def test_public_review_gate(tmp_path, field, value):
    folder = tmp_path/'demo/public'
    review = write_bundle(folder, make_packet(), approved_test_double=True)
    review[field] = value
    (tmp_path/'OWNER_APPROVAL.json').write_text(json.dumps(review))
    with pytest.raises(ValueError):
        load_public(tmp_path)


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(responses=[{'model':'TEST_ONLY','evidence_mode':'LIVE_LLM'}]),
    lambda p:p.update(responses=[{'model':'TEST_ONLY','evidence_mode':'TEST_ONLY','actual_requests':1}]),
    lambda p:p.update(responses=[{'model':'TEST_ONLY','evidence_mode':'TEST_ONLY','simulation_status':'EXECUTED'}]),
    lambda p:p['task'].update(provider='DeepSeek'),
    lambda p:p['task'].update(M5_enabled=True),
    lambda p:p['task'].update(client_mode='LIVE_LLM'),
    lambda p:p['summary'].update(task_id='WRONG'),
    lambda p:p.update(posthoc_review={'POSTHOC_VALIDATOR_REVIEW':'PASSED'}),
    lambda p:p.update(original_guard={'passed':True}),
    lambda p:p['task']['plan'].update(goals=Goals().model_dump()),
    lambda p:p['task']['calls'][0]['output'].update(M5_enabled=True),
    lambda p:p['task']['calls'][0].update(execution_status='EXECUTED'),
    lambda p:p['task']['calls'][0].update(elapsed_seconds=1.0),
    lambda p:p['task']['calls'][0].update(name='request_model_update'),
    lambda p:p['task']['calls'][0].update(id='TEST_ONLY_CALL_1'),
    lambda p:p['task']['calls'][1]['output']['data']['candidates'][0]['performance_compliance'].update(prediction=1.0),
    lambda p:p['task']['calls'][1]['output']['data']['candidates'][0]['performance_compliance'].update(status='verified'),
    lambda p:p['task']['calls'][0]['output']['data']['evidence_preview'][0].update(delta_T_ad_K=1.0),
    lambda p:p['task']['calls'][2]['output']['data'].update(structure_verified=True),
    lambda p:p['task']['calls'][3]['arguments']['findings'][0].update(call_id='MISSING'),
    lambda p:p['task']['calls'][3]['arguments']['findings'][0].update(pointer='/nonexistent'),
])
def test_public_approval_cannot_override_scientific_boundaries(tmp_path, mutation):
    packet = make_packet(); mutation(packet)
    write_bundle(tmp_path/'demo/public', packet, approved_test_double=True)
    with pytest.raises((ValueError, KeyError)):
        load_public(tmp_path)


@pytest.mark.parametrize('field,value',[
    ('status','APPROVED'), ('independent_reviewer','TEST_ONLY_FAKE'),
    ('owner_approval','TEST_ONLY_FAKE'), ('payload_sha256','0'*64),
])
def test_pending_preview_rejects_signature_or_hash_changes(tmp_path, field, value):
    review = write_bundle(tmp_path/'candidate', make_packet())
    review[field] = value
    (tmp_path/'candidate/review.json').write_text(json.dumps(review))
    with pytest.raises(ValueError): load_candidate(tmp_path/'candidate')


def test_pending_bundle_cannot_enter_public_gate(tmp_path):
    write_bundle(tmp_path/'demo/public', make_packet())
    assert load_public(tmp_path) is None


def test_ambiguous_chemical_receipt_is_unknown(tmp_path):
    packet = make_packet(); write_bundle(tmp_path/'candidate', packet)
    loaded = load_candidate(tmp_path/'candidate')
    loaded['task']['calls'].append(deepcopy(loaded['task']['calls'][2]))
    detail = story_view(loaded)['details'][0]
    assert detail['receipt'] is None and detail['row']['独立chemical_pass'] is None


def test_symlink_payload_rejected(tmp_path):
    folder = tmp_path/'demo/public'
    write_bundle(folder, make_packet(), approved_test_double=True)
    (folder/'payload.json').rename(folder/'saved.json')
    (folder/'payload.json').symlink_to(folder/'saved.json')
    with pytest.raises(ValueError): load_public(tmp_path)


def test_frozen_goals_and_model_plan_separation():
    goals = Goals(exclude_elements=['Pb'])
    assert goals.temperature_min_K is None and goals.e_end_kV_cm is None
    with pytest.raises(ValidationError): goals.maximize = True
    with pytest.raises(ValidationError): PlanningProposal(steps=['search_material_evidence'],goals={})
    schema = definitions(planning=True, frozen_goals=goals.model_dump())[0]['parameters']
    assert 'goals' not in schema['properties'] and schema['additionalProperties'] is False
    assert schema['properties']['clarification'] == {'type':'null'}


@pytest.mark.parametrize('kwargs',[
    {'temperature_min_K':-1}, {'temperature_min_K':float('nan')},
    {'temperature_min_K':320,'temperature_max_K':300},
    {'e_min_kV_cm':20,'e_max_kV_cm':10},
    {'include_elements':['Pb'],'exclude_elements':['Pb']}, {'include_elements':['Unknown']},
])
def test_invalid_goals_rejected(kwargs):
    with pytest.raises((ValueError, ValidationError)): Goals(**kwargs)


def test_m5_contract_is_literal_false():
    with pytest.raises(ValidationError): ToolOutput(M5_enabled=True)


def test_launcher_local_only_and_no_credentials(monkeypatch):
    from streamlit.web import cli
    import socket
    module = runpy.run_path(str(ROOT/'scripts/run_readonly_offline.py'),run_name='TEST_ONLY_IMPORT')
    monkeypatch.setattr(sys,'argv',['run_readonly_offline.py','--port','8519'])
    monkeypatch.setenv('DEEPSEEK_API_KEY','TEST_ONLY_PLACEHOLDER')
    def fake_cli():
        import os
        assert 'DEEPSEEK_API_KEY' not in os.environ
        assert '--server.address=127.0.0.1' in sys.argv
        assert '--browser.gatherUsageStats=false' in sys.argv
        for call in [lambda:socket.getaddrinfo('example.invalid',443),
                     lambda:socket.socket().connect(('198.51.100.1',443)),
                     lambda:socket.socket().connect_ex(('198.51.100.1',443))]:
            with pytest.raises(OSError): call()
    monkeypatch.setattr(cli,'main',fake_cli)
    module['main']()


def test_readonly_import_closure_excludes_execution_modules():
    # Test collection may import the core; use a fresh interpreter for UI isolation.
    import subprocess
    code = "import sys; sys.path[:0]=['src','app']; import readonly_demo; " + \
           "assert not any(n.startswith(('ecalx.agent.runtime','ecalx.agent.siliconflow','ecalx.agent.llm','ecalx.agent.tools','ecalx.ml','ecalx.experiments.virtual','ecalx.experiments.real')) for n in sys.modules)"
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('parameter',['X-Amz-Signature','X-Goog-Signature','sig','access_token'])
def test_public_rejects_authorized_download_urls(tmp_path, parameter):
    packet = make_packet()
    packet['task']['original'] = 'https://example.invalid/file?'+parameter+'=TEST_ONLY'
    write_bundle(tmp_path/'demo/public', packet, approved_test_double=True)
    with pytest.raises(ValueError): load_public(tmp_path)


def test_public_rejects_private_path_and_credential_fields(tmp_path):
    packet = make_packet()
    packet['task']['original'] = chr(47)+'Users'+chr(47)+'TEST_ONLY'+chr(47)+'file'
    write_bundle(tmp_path/'path/demo/public', packet, approved_test_double=True)
    with pytest.raises(ValueError): load_public(tmp_path/'path')
    packet = make_packet(); packet['task']['access_token'] = 'TEST_ONLY'
    write_bundle(tmp_path/'field/demo/public', packet, approved_test_double=True)
    with pytest.raises(ValueError): load_public(tmp_path/'field')
