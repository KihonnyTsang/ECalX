"""0.2.0b3 release gates: complete delta queries and truthful report UI."""
import json
from pathlib import Path
import tomllib

import pytest
from streamlit.testing.v1 import AppTest
from ecalx_local.import_data import preview, commit
from ecalx_local.inputs import prepare_inputs
from ecalx_local.engine import LocalAgent, LocalWorkflow, CORE
from ecalx_local.jobs import create_research
from ecalx_local.presentation import report_markdown
from ecalx_local.conversation import Conversation, fingerprint

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('value', ['10', '20', '30', '40', '50', '12.5', '0.125', '0.001'])
def test_imported_delta_through_real_toolchain(isolation, tmp_path, value):
    values = ['10', '20', '30', '40', '50', '12.5', '0.125', '0.001']
    raw = ('formula,e_delta,field_unit,url,data_kind\n' + ''.join(
        f'BaTiO3,{v},kV/cm,https://example.invalid/SYNTHETIC_TEST_DATA/{v},SYNTHETIC_TEST_DATA\n'
        for v in values)).encode()
    mapping = {key: key for key in ('formula', 'e_delta', 'field_unit', 'url', 'data_kind')}
    p = preview(raw, 'SYNTHETIC_TEST_DATA-release.csv', mapping)
    assert not p['errors']
    commit(raw, p, isolation)
    agent = LocalAgent(tmp_path/'tasks')
    prepared = prepare_inputs({'natural_language': '探索候选组成', 'e_delta': value})
    task = create_research(prepared, 'local', confirmed=True, storage=agent.storage_root)
    agent.advance(task['id'], LocalWorkflow())
    assert set(agent.get(task['id'])['plan']['steps']) == CORE
    agent.confirm_plan(task['id'], confirmed=True)
    done = agent.run_until_wait(task['id'], LocalWorkflow())
    assert done['status'] == 'COMPLETED', done.get('error')
    search = next(c for c in done['calls'] if c['name'] == 'search_material_evidence')['output']['data']
    assert search['match_counts']['delta_E_matches'] == 1
    assert search['condition_evidence']['all_goal_matches'] == 1
    matches = [item['evidence'] for k in ('candidates', 'evidence_only') for item in search[k]]
    assert len(matches) == 1 and matches[0]['e_delta_kV_cm'] == float(value)
    assert matches[0]['e_start_kV_cm'] is None and matches[0]['e_end_kV_cm'] is None
    generated = next(c for c in done['calls'] if c['name'] == 'generate_material_candidates')['output']['data']['candidates']
    assert generated and all(c['status'] == 'generated_unverified' for c in generated)
    checks = [c for c in done['calls'] if c['name'] == 'validate_chemical_constraints']
    assert len(checks) == len(generated) and all(c['output']['data']['chemical_pass'] for c in checks)
    assert done['report']['faithfulness']['passed'] and not done['virtual_ids'] and not done['real_ids']
    report = report_markdown(done)
    assert 'unknown' in report and 'SYNTHETIC_TEST_DATA' in report
    frozen = fingerprint(done)
    conversation = Conversation(agent.storage_root)
    for question in ['总结依据', '还缺哪些证据？', '这些候选有什么限制？']:
        assert conversation.create(task['id'], question)['status'] == 'COMPLETED'
    assert fingerprint(agent.get(task['id'])) == frozen
    assert not (isolation/'api_usage.sqlite3').exists()


def test_release_version_and_auxiliary_evidence_downloads(isolation, tmp_path, monkeypatch):
    from ecalx import __version__
    assert __version__ == tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version'] == '0.2.0b3'
    assert (ROOT/'app.py').read_bytes() == (ROOT/'ecalx_local/app.py').read_bytes()
    raw = b'formula,e_delta,field_unit,url,data_kind\nBaTiO3,20,kV/cm,https://example.invalid/SYNTHETIC_TEST_DATA,SYNTHETIC_TEST_DATA\n'
    p = preview(raw, 'SYNTHETIC_TEST_DATA.csv', {k: k for k in ('formula', 'e_delta', 'field_unit', 'url', 'data_kind')})
    commit(raw, p, isolation)
    agent = LocalAgent(tmp_path/'tasks')
    task = create_research(prepare_inputs({'e_start': '20', 'e_end': '0'}), 'local', confirmed=True, storage=agent.storage_root)
    agent.advance(task['id'], LocalWorkflow()); agent.confirm_plan(task['id'], confirmed=True)
    done = agent.run_until_wait(task['id'], LocalWorkflow())
    assert done['status'] == 'COMPLETED'
    before = json.dumps(done, sort_keys=True)
    import ecalx_local
    monkeypatch.setattr(ecalx_local, 'STORAGE', agent.storage_root)
    app = AppTest.from_file(str(ROOT/'app.py')).run(timeout=20)
    app.session_state.task_id = task['id']; app.run()
    assert not app.exception
    assert any('<h1>材料研究</h1>' in m.value for m in app.markdown)
    assert not any('目标条件下无匹配实验依据' in e.value for e in app.error)
    app.session_state['result_tab_'+task['id']]='文献依据';app.run()
    assert any('幅度' in w.value for w in app.warning)
    app.session_state['result_tab_'+task['id']]='报告与详情';app.run()
    # Deferred download widgets are still available on the report tab.
    labels = [b.proto.label for b in app.get('download_button')]
    assert '下载研究报告' in labels and '下载原始数据' in labels
    assert json.dumps(agent.get(task['id']), sort_keys=True) == before
