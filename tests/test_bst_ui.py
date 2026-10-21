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


def open_bst(tmp_path, monkeypatch, entry='app.py'):
    import ecalx_local
    monkeypatch.setattr(ecalx_local, 'STORAGE', tmp_path/'tasks')
    at = AppTest.from_file(str(ROOT/entry)).run(timeout=20)
    assert not at.exception
    at.radio(key='main_navigation').set_value('BST电卡插值').run()
    assert not at.exception
    return at


def has_result(at):
    return any('ΔT插值估计' in h.value or 'ΔT文献原始读图值' in h.value for h in at.subheader)


def test_packaged_numerics_and_supplied_reference(reference_csv):
    model = load_interpolator()
    assert model.__class__.__module__ == 'ecalx_local.bst_interpolate'
    assert load_interpolator(reference_csv.read_bytes()).curves == model.curves
    point = model.predict('0.20', '323', '30')
    assert point['delta_T_magnitude_K'] == pytest.approx(1.539950495049505)
    assert result_heading(point) == 'ΔT插值估计：约1.54 K'


@pytest.mark.parametrize('entry', ['app.py', 'ecalx_local/app.py'])
def test_real_shell_example_and_three_kinds(tmp_path, monkeypatch, entry, reference_csv):
    at = open_bst(tmp_path, monkeypatch, entry)
    assert at.subheader[0].value == 'ΔT插值估计：约1.54 K'
    assert any('结果类型：组成插值' == x.value for x in at.markdown)
    rows = at.table[0].value.to_dict('records')
    assert [r['参考组成 x'] for r in rows] == ['0.15', '0.25']
    assert [r['当前温度取值方式'] for r in rows] == ['文献原始读图值', '温度插值']
    assert [r['图号'] for r in rows] == ['图8B', '图8C']
    assert len(at.table[1].value) == 3
    at.text_input(key='bst_x').set_value('0.15').run()
    assert at.subheader[0].value == 'ΔT文献原始读图值：约1.43 K'
    at.text_input(key='bst_temperature').set_value('300').run()
    assert any(x.value == '结果类型：温度插值' for x in at.markdown)
    assert not at.exception


def test_no_stale_value_or_chart_after_unsupported_input(tmp_path, monkeypatch, reference_csv):
    at = open_bst(tmp_path, monkeypatch)
    for x, t, e in [
        ('0.049999999', '323', '30'), ('0.350000001', '323', '30'),
        ('0.20', '242.999999', '30'), ('0.20', '402.900001', '30'),
        ('0.20', '323', '15'), ('0.20', '323', '30.000001'),
        ('0.20', '323', '-30'), ('NaN', '323', '30'),
        ('0.20', 'Infinity', '30'), ('0.20', '323', 'abc'),
    ]:
        at.text_input(key='bst_x').set_value(x)
        at.text_input(key='bst_temperature').set_value(t)
        at.text_input(key='bst_field').set_value(e).run()
        assert not at.exception and at.warning and '不支持此输入' in at.warning[0].value
        assert not has_result(at) and not at.table
        assert not any('vega' in node.type for node in at)
    at.text_input(key='bst_x').set_value('0.20')
    at.text_input(key='bst_field').set_value('30')
    for t in ['243', '402.9']:
        at.text_input(key='bst_temperature').set_value(t).run()
        assert not at.exception and not at.warning and has_result(at)
    at.text_input(key='bst_temperature').set_value('').run()
    assert at.info and not has_result(at) and not at.table


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


def test_other_research_conditions_and_draft_survive_bst_navigation(tmp_path, monkeypatch, reference_csv):
    at = open_bst(tmp_path, monkeypatch)
    at.radio(key='main_navigation').set_value('研究与历史').run()
    at.text_area(key='home_question').set_value('研究含Zr的BZT陶瓷')
    at.text_input(key='home_edelta').set_value('100')
    next(b for b in at.button if b.label == '检查研究条件').click().run()
    prepared = deepcopy(at.session_state['prepared'])
    assert prepared['goals']['delta_E_kV_cm'] == 100
    at.radio(key='main_navigation').set_value('BST电卡插值').run()
    at.text_input(key='bst_field').set_value('100').run()
    assert at.warning and not has_result(at)
    at.radio(key='main_navigation').set_value('研究与历史').run()
    assert not at.exception and at.session_state['prepared'] == prepared
    at.radio(key='main_navigation').set_value('BST电卡插值').run()
    assert at.text_input(key='bst_field').value == '100'
    assert not list((tmp_path/'tasks').glob('*/task.json'))


def test_missing_or_invalid_data_only_disables_bst(tmp_path, monkeypatch):
    monkeypatch.delenv('ECALX_BST_REFERENCE_CSV', raising=False)
    at = open_bst(tmp_path, monkeypatch)
    assert at.warning and '尚未提供参考数据' in at.warning[0].value and not has_result(at)
    monkeypatch.setenv('ECALX_BST_REFERENCE_CSV', str(tmp_path/'absent-private-directory.csv'))
    at.run()
    assert not at.exception and at.warning and not has_result(at)
    assert str(tmp_path) not in at.warning[0].value
    local = tmp_path/'invalid.csv'
    local.write_bytes(b'observation_id,temperature_K\nTEST_ONLY,323\n')
    monkeypatch.setenv('ECALX_BST_REFERENCE_CSV', str(local))
    at.run()
    assert not at.exception and at.warning and not has_result(at)
    at.radio(key='main_navigation').set_value('研究与历史').run()
    assert not at.exception and any(b.label == '检查研究条件' for b in at.button)
    with pytest.raises(BSTUnavailable):
        load_interpolator(b'invalid upload overrides even a configured path')


def test_bst_deep_link(tmp_path, monkeypatch, reference_csv):
    import ecalx_local
    monkeypatch.setattr(ecalx_local, 'STORAGE', tmp_path/'tasks')
    at = AppTest.from_file(str(ROOT/'app.py'))
    at.query_params['view'] = 'bst'
    at.run(timeout=20)
    assert not at.exception and at.radio(key='main_navigation').value == 'BST电卡插值'
    assert has_result(at)
