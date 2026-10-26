"""Reference/evidence isolation and reuse of unchanged strict interpolation."""
import hashlib
import json
from copy import deepcopy

import pytest
from test_bst_ui import reference_csv
from ecalx_local.bst_reference import import_reference, metadata, reference_path
from ecalx_local.bst_research import eligibility, calculate
from ecalx_local.bst_tool import load_interpolator
from ecalx_local.inputs import prepare_inputs
from ecalx_local.bst_interpolate import SOURCE_SHA256


def prepared(formula='Ba0.8Sr0.2TiO3', temperature='323', field='30'):
    return prepare_inputs({'composition': formula, 'temperature_min': temperature,
        'temperature_max': temperature, 'temperature_unit': 'K', 'e_delta': field})


def test_idempotent_import_preserves_frozen_data_and_separates_evidence(tmp_path, reference_csv):
    raw = reference_csv.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    manifest = import_reference(raw, tmp_path)
    saved = reference_path(tmp_path)
    original_stat = saved.stat()
    assert import_reference(raw, tmp_path) == manifest == metadata(tmp_path)
    assert saved.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert saved.read_bytes() == raw and source_hash == SOURCE_SHA256
    assert saved.stat().st_mode & 0o777 == 0o600
    assert manifest['point_count'] == 204 and manifest['curve_count'] == 12
    assert manifest['paper_count'] == 1 and manifest['independent_experiment_count'] is None
    assert not manifest['experimentally_verified']
    assert not (tmp_path/'data/evidence.sqlite3').exists()
    assert str(tmp_path) not in json.dumps(manifest)
    assert load_interpolator(root=tmp_path).predict('.2',323,30)['delta_T_magnitude_K'] == pytest.approx(1.539950495049505)
    with pytest.raises(ValueError):
        import_reference(raw+b'\n', tmp_path)
    assert saved.read_bytes() == raw


def test_all_existing_boundaries_and_protocols(tmp_path, reference_csv):
    import_reference(reference_csv.read_bytes(), tmp_path)
    p = prepared()
    original = deepcopy(p)
    assert eligibility(p, root=tmp_path)['status'] == 'available'
    assert calculate(p, root=tmp_path)['point']['delta_T_magnitude_K'] == pytest.approx(1.539950495049505)
    assert p == original
    for x, t, e in [('.049999999','323','30'),('.350000001','323','30'),
                     ('.2','242.999999','30'),('.2','402.900001','30'),('.2','323','15'),
                     ('.2','323','30.000001')]:
        from decimal import Decimal
        trial = prepared(f'Ba{1-Decimal(x)}Sr{x}TiO3', t, e)
        assert eligibility(trial, root=tmp_path)['status'] == 'unsupported'
        with pytest.raises(ValueError):
            calculate(trial, root=tmp_path)
    for t in ['243', '402.9']:
        assert eligibility(prepared(temperature=t), root=tmp_path)['status'] == 'available'
    for formula in ['PbZr0.52Ti0.48O3', 'Ba0.8Sr0.2Ti0.9Zr0.1O3', 'Ba0.8Sr0.2TiO2.9']:
        assert eligibility(prepared(formula), root=tmp_path)['status'] == 'not_bst'
    for text in ['BST薄膜', 'BST单晶', 'BST polymer']:
        trial = prepared(); trial['raw']['natural_language'] = text
        assert eligibility(trial, root=tmp_path)['status'] == 'unsupported'
    trial = prepared(); trial['goals'].update(e_start_kV_cm=0, e_end_kV_cm=30)
    assert eligibility(trial, root=tmp_path)['status'] == 'unsupported'
    trial['goals'].update(e_start_kV_cm=30, e_end_kV_cm=0)
    assert eligibility(trial, root=tmp_path)['status'] == 'available'


def test_non_bst_and_unrestricted_empty_conditions_need_no_reference(tmp_path):
    p = prepare_inputs({'composition': 'PbZr0.52Ti0.48O3', 'temperature_min': '-20.5',
        'temperature_max': '80.25', 'temperature_unit': '°C', 'e_delta': '100'})
    assert eligibility(p, root=tmp_path)['status'] == 'not_bst'
    assert eligibility(prepare_inputs({}), root=tmp_path)['status'] == 'not_bst'
    assert eligibility(prepared(), root=tmp_path)['status'] == 'missing_data'


def test_attach_to_existing_task_survives_worker_save(isolation, tmp_path, reference_csv):
    from ecalx_local.engine import LocalAgent
    from ecalx_local.jobs import create_research
    from ecalx_local.presentation import report_markdown
    import_reference(reference_csv.read_bytes(), isolation)
    p = prepared()
    agent = LocalAgent(tmp_path/'tasks')
    task = create_research(p, 'local', confirmed=True, storage=agent.storage_root)
    stale_worker_copy = deepcopy(task)
    result = calculate(p, root=isolation)
    agent.save_literature_result(task['id'], result)
    agent.save(stale_worker_copy)
    saved = agent.get(task['id'])
    assert saved['literature_results'] == [result]
    assert saved['frozen_goals'] == task['frozen_goals'] and saved['calls'] == task['calls']
    assert agent.public(saved)['literature_results'] == [result]
    assert '约1.54 K' in report_markdown(saved)
    assert not agent.tools.repository.records()
