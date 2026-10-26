"""Real local BST prototype integration, coverage refusal and workspace isolation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import os

import pytest
from streamlit.testing.v1 import AppTest

from ecalx_local.bst_tool import BSTUnavailable, load_interpolator
from ecalx_local.bst_interpolate import SOURCE_SHA256
from ecalx_local.bst_ui import curve_chart, result_heading


ROOT = Path(__file__).resolve().parents[1]
@pytest.fixture
def reference_csv(monkeypatch):
    supplied = os.environ.get('ECALX_BST_TEST_CSV')
    if not supplied:
        pytest.skip('Provide an authorized reference CSV via ECALX_BST_TEST_CSV for numerical integration tests')
    path = Path(supplied).resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SOURCE_SHA256
    monkeypatch.setenv('ECALX_BST_REFERENCE_CSV', str(path))
    return path


def test_packaged_numerics_and_supplied_reference(reference_csv):
    model = load_interpolator()
    assert model.__class__.__module__ == 'ecalx_local.bst_interpolate'
    assert load_interpolator(reference_csv.read_bytes()).curves == model.curves
    point = model.predict('0.20', '323', '30')
    assert point['delta_T_magnitude_K'] == pytest.approx(1.539950495049505)
    assert result_heading(point) == 'ΔT 插值估计：约 1.54 K'


def test_chart_preserves_nodes_and_contains_only_safe_display_data(reference_csv):
    model = load_interpolator()
    for x in ['0.20', '0.15']:
        point = model.predict(x, '323', '30')
        curve = model.curve(x, '30', include_temperature='323')
        spec = curve_chart(point, curve, model.reference_rows(x, '30')).to_dict()
        rows = next(iter(spec['datasets'].values()))
        result_rows = [r for r in rows if r['类型'] == '组成插值' and r['系列'] != '当前输入']
        if x == '0.20':
            assert [(r['温度'], r['ΔT']) for r in result_rows] == [
                (r['temperature_K'], r['delta_T_magnitude_K']) for r in curve]
        else:
            assert not result_rows
        lo, hi = point['temperature_coverage_K']
        assert all(lo <= r['温度'] <= hi for r in rows)
        assert all(set(r) == {'温度', 'ΔT', '系列', '类型', '组成', '说明'} for r in rows)
        assert len([r for r in rows if r['系列'] == '当前输入']) == 1
        assert '1.53995' not in ' '.join(r['说明'] for r in rows)
        assert not any(token in json.dumps(spec) for token in ['source_pdf_path', 'original_workspace_csv', 'Dai_Fig8_original.png'])



def open_workbench(tmp_path, monkeypatch, entry='app.py'):
    import ecalx_local
    monkeypatch.setattr(ecalx_local, 'STORAGE', tmp_path/'tasks')
    at = AppTest.from_file(str(ROOT/entry)).run(timeout=20)
    assert not at.exception
    assert not any(b.label=='BST电卡插值' for b in at.button)
    return at


def fill_conditions(at, formula='Ba0.8Sr0.2TiO3', t='323', field='30'):
    at.text_input(key='home_composition').set_value(formula)
    at.text_input(key='home_tmin').set_value(t)
    at.text_input(key='home_tmax').set_value(t)
    at.selectbox(key='home_tunit').set_value('K')
    at.text_input(key='home_edelta').set_value(field).run()
    assert not at.exception
    return at


def click(at, label):
    next(b for b in at.button if b.label == label).click().run(timeout=20)
    assert not at.exception


@pytest.mark.parametrize('entry', ['app.py', 'ecalx_local/app.py'])
def test_workbench_without_upload_and_saved_history(tmp_path, monkeypatch, entry, reference_csv, isolation):
    from ecalx_local.bst_reference import import_reference
    from ecalx_local.engine import LocalAgent, LocalWorkflow
    from ecalx_local.presentation import report_markdown
    import_reference(reference_csv.read_bytes(), isolation)
    monkeypatch.delenv('ECALX_BST_REFERENCE_CSV')
    at = fill_conditions(open_workbench(tmp_path, monkeypatch, entry))
    assert any(b.label=='计算电卡温变' for b in at.button)
    assert not at.get('file_uploader')
    click(at, '计算电卡温变')
    assert any(h.value == 'ΔT 插值估计：约 1.54 K' for h in at.subheader)
    assert [r['参考组成 x'] for r in at.table[0].value.to_dict('records')] == ['0.15', '0.25']
    click(at, '开始研究')
    at.checkbox(key='home_confirmed').set_value(True).run()
    click(at, '确认并开始研究')
    tid = at.session_state['task_id']
    agent = LocalAgent(tmp_path/'tasks')
    task = agent.get(tid)
    assert task['literature_results'][0]['point']['delta_T_magnitude_K'] == pytest.approx(1.539950495049505)
    assert not agent.tools.repository.records()  # Literature never enters experiments.
    assert '文献曲线插值' in report_markdown(task)
    assert not at.get('file_uploader')
    # Finish the existing offline toolchain; worker saves retain the local receipt.
    from ecalx_local.jobs import worker_status
    import time
    for _ in range(80):
        task = agent.get(tid)
        if task['plan'] and worker_status(tid, agent.storage_root)['state'] != 'running':
            break
        time.sleep(.05)
    agent.confirm_plan(tid, confirmed=True)
    done = agent.run_until_wait(tid, LocalWorkflow())
    assert done['status'] == 'COMPLETED'
    assert done['literature_results'] == task['literature_results']
    fresh = AppTest.from_file(str(ROOT/entry))
    fresh.query_params['task'] = tid
    fresh.run(timeout=20)
    assert not fresh.exception
    assert any('约 1.54 K' in h.value for h in fresh.subheader)
    assert any('已随研究历史保存' in c.value for c in fresh.caption)
    assert not (isolation/'api_usage.sqlite3').exists()


def test_no_stale_result_and_non_bst_remains_free(tmp_path, monkeypatch, reference_csv):
    at = fill_conditions(open_workbench(tmp_path, monkeypatch))
    click(at, '计算电卡温变')
    assert any('约 1.54 K' in h.value for h in at.subheader)
    for formula, t, e in [('Ba0.8Sr0.2TiO3','403','30'), ('Ba0.8Sr0.2TiO3','323','15'),
                           ('Ba0.96Sr0.04TiO3','323','30'), ('PbZr0.52Ti0.48O3','323','100')]:
        fill_conditions(at, formula, t, e)
        assert not any('ΔT 插值估计' in h.value for h in at.subheader)
        assert not at.table and not any('vega' in node.type for node in at)
        assert any(b.label == '开始研究' and not b.disabled for b in at.button)
    click(at, '开始研究')
    prepared = deepcopy(at.session_state['prepared'])
    assert prepared['goals']['delta_E_kV_cm'] == 100
    assert not prepared.get('literature_results')
    assert any(b.label == '确认并开始研究' for b in at.button)


def test_missing_data_only_disables_interpolation(tmp_path, monkeypatch):
    monkeypatch.delenv('ECALX_BST_REFERENCE_CSV', raising=False)
    at = fill_conditions(open_workbench(tmp_path, monkeypatch))
    assert any(b.label=='设置 → 数据库构建' for b in at.button)
    assert not any('ΔT 插值估计' in h.value for h in at.subheader)
    click(at, '开始研究')
    assert any(b.label == '确认并开始研究' for b in at.button)
    with pytest.raises(BSTUnavailable):
        load_interpolator(b'invalid upload')


def test_legacy_bst_link_opens_unrestricted_workbench(tmp_path, monkeypatch):
    at = open_workbench(tmp_path, monkeypatch)
    at.query_params['view'] = 'bst'
    at.run()
    assert not at.exception and at.session_state['main_navigation'] == '研究与历史'
    assert any(b.label == '开始研究' for b in at.button)
