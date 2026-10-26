"""Candidate links use real frozen curves and never reinterpret M4 scores."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from test_bst_ui import reference_csv
from ecalx_local.inputs import prepare_inputs
from ecalx_local.bst_reference import import_reference
from ecalx_local.bst_tool import load_interpolator
from ecalx_local.candidate_delta_t import candidate_results, evaluation_conditions


def task_and_cards():
    p = prepare_inputs({'allowed_elements': 'Ba Sr Ca Ti O', 'exclude': 'Pb'})
    t = {'id': 'TEST_ONLY_delta_cards', 'calls': [], 'frozen_goals': p['goals'], 'input_snapshot': p,
         'simulation_runs': [{'experiment_id': 'TEST_ONLY_existing_run', 'report': {'experiment': {
             'conditions': {'temperature_K': 303., 'e_start_kV_cm': 0., 'e_end_kV_cm': 20.}}}}]}
    cards = [{'id': str(i), 'formula': formula} for i, formula in enumerate([
        'Ba0.95Sr0.05TiO3', 'Ba0.9Sr0.1TiO3', 'Ba0.85Sr0.15TiO3',
        'Ba0.8Sr0.2TiO3', 'Ba0.75Sr0.25TiO3', 'Ba0.95Ca0.05TiO3'])]
    return t, cards


def test_five_real_curve_results_remain_independent_of_loading_protocol(tmp_path, reference_csv):
    import_reference(reference_csv.read_bytes(), tmp_path)
    task, cards = task_and_cards()
    before = deepcopy(task)
    results = candidate_results(task, cards, root=tmp_path)
    original = load_interpolator(root=tmp_path)
    for card, x in zip(cards[:5], ['.05', '.1', '.15', '.2', '.25']):
        result = results[card['id']]
        literature = result['literature']
        assert literature['point'] == original.predict(x, 303, 20)
        assert literature['status'] == 'reference_only'
        assert not literature['protocol_match'] and not literature['experimentally_measured']
        assert literature['point']['target_definition'] == 'published_positive_cooling_magnitude_after_field_removal'
        assert result['primary'] is None and result['model']['status'] == 'unavailable'
        assert result['conditions']['field_protocol']['start_kV_cm'] == 0
        assert result['conditions']['field_protocol']['end_kV_cm'] == 20
    assert results['5']['literature']['point'] is None
    assert 'Ca' in results['5']['literature']['reason']
    assert task == before


@pytest.mark.parametrize('formula', ['Ba0.6Sr0.4TiO3', 'Ba0.96Sr0.04TiO3',
    'Ba0.8Sr0.2TiO2.9', 'Ba0.8Sr0.1Ca0.1TiO3', 'PbTiO3'])
def test_no_composition_or_oxygen_extrapolation(tmp_path, reference_csv, formula):
    import_reference(reference_csv.read_bytes(), tmp_path)
    task, _ = task_and_cards()
    result = candidate_results(task, [{'id': 'out', 'formula': formula}], root=tmp_path)['out']
    assert result['literature']['point'] is None and result['primary'] is None


def test_no_stale_run_or_temperature_midpoint_or_missing_reference(tmp_path, reference_csv, monkeypatch):
    monkeypatch.delenv('ECALX_BST_REFERENCE_CSV')
    task, cards = task_and_cards()
    assert '尚未导入参考数据' in candidate_results(task, cards[:1], root=tmp_path)['0']['literature']['reason']
    import_reference(reference_csv.read_bytes(), tmp_path)
    task['frozen_goals'].update(temperature_min_K=310., temperature_max_K=320.)
    assert candidate_results(task, cards[:1], root=tmp_path)['0']['literature']['point'] is None
    task['simulation_runs'] = []
    assert evaluation_conditions(task)['temperature_K'] is None
    assert candidate_results(task, cards[:1], root=tmp_path)['0']['literature']['point'] is None


@pytest.mark.parametrize('temperature,field', [(100.,20.),(303.,15.),(900.,20.)])
def test_original_temperature_and_field_boundaries(tmp_path, reference_csv, temperature, field):
    import_reference(reference_csv.read_bytes(), tmp_path)
    task, cards = task_and_cards()
    task['simulation_runs'][-1]['report']['experiment']['conditions'].update(
        temperature_K=temperature, e_end_kV_cm=field)
    assert candidate_results(task, cards[:1], root=tmp_path)['0']['literature']['point'] is None


def test_personal_model_cannot_erase_latest_explicit_simulation_path(monkeypatch):
    import ecalx_local.candidate_delta_t as adapter
    monkeypatch.setattr(adapter, 'PERSONAL', True)
    task, cards = task_and_cards()
    results = candidate_results(task, cards[:1], receipts=[])
    assert results['0']['model']['status'] == 'rejected'
    assert results['0']['primary'] is None


@pytest.mark.parametrize('material', ['thin_film', 'single_crystal', 'polymer', 'BST薄膜'])
def test_reference_cannot_claim_unsupported_sample_form(tmp_path, reference_csv, material):
    import_reference(reference_csv.read_bytes(), tmp_path)
    task, cards = task_and_cards()
    task['frozen_goals']['material_type'] = material
    assert candidate_results(task, cards[:1], root=tmp_path)['0']['literature']['point'] is None


def test_ui_has_distinct_units_provenance_and_unavailable_ca(tmp_path, reference_csv, isolation):
    from test_performance_recovery import generated_task
    from ecalx_local.element_research import create_simulation, advance_simulation
    from ecalx_local.presentation import candidate_cards
    agent, task = generated_task(tmp_path)
    run = create_simulation(agent, task, 303., 0., 20.)
    advance_simulation(agent, task, run, all_remaining=True)
    task = agent.get(task['id'])
    import_reference(reference_csv.read_bytes(), isolation)
    before = deepcopy(task)
    results = candidate_results(task, candidate_cards(task))
    app = AppTest.from_string('''from ecalx_local.engine import LocalAgent
from ecalx_local.presentation import candidate_cards
from ecalx_local.ui_research import candidates
a=LocalAgent('''+repr(str(agent.storage_root))+''')
t=a.get('''+repr(task['id'])+''')
candidates(t,candidate_cards(t))
''').run(timeout=15)
    assert not app.exception
    delta = [m for m in app.metric if m.label == '文献插值 ΔT']
    scores = [m for m in app.metric if m.label == '模拟评分']
    assert len(delta) == len(scores) == 4
    assert all(m.value.endswith(' K') for m in delta)
    assert all(not m.value.endswith(' K') for m in scores)
    expected = {f"{r['literature']['point']['delta_T_magnitude_K']:.3f} K" for r in results.values() if r['literature']['point']}
    assert {m.value for m in delta} <= expected
    assert all('暂无可用ΔT估计' not in m.value and 'RF/XGBoost' not in m.value for m in app.caption)
    assert sum('模拟排名 #1' in m.value for m in app.markdown) == 1
    assert len([e for e in app.expander if e.label=='查看详情'])==4
    assert not app.code and not app.json
    assert all(len(m.value.split('.')[-1])==3 for m in scores)
    app.number_input[0].set_value(2).run()
    assert not app.exception
    assert len(app.metric)==2  # fifth BST has ΔT and score; Ca cards have neither.
    assert not any('不可用' in m.value or '暂无' in m.value for m in app.caption)
    app.number_input[0].set_value(3).run()
    assert not app.exception and not app.metric
    assert not any('不可用' in m.value or '暂无' in m.value for m in app.caption)
    assert agent.get(task['id']) == before
