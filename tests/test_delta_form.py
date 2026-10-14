"""Regression of the ordinary ΔE form through real local research tools."""
import json
from dataclasses import replace
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
import ecalx_local  # Select BYO/personal source before importing scientific modules.
from ecalx.agent.contracts import Goals
from ecalx.data.schema import EvidenceRecord
from ecalx_local.inputs import prepare_inputs
from ecalx_local.conditions import condition_summary
from ecalx_local.engine import LocalAgent, LocalWorkflow
from ecalx_local.jobs import create_research

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('value', ['10','20','30','40','50','12.5','0.125','1e-3'])
def test_delta_only_matches_missing_endpoints(value):
    p = prepare_inputs({'e_delta':value})
    g = p['goals']
    assert g['delta_E_kV_cm'] == float(value)
    assert all(g[k] is None for k in ('e_start_kV_cm','e_end_kV_cm','e_min_kV_cm','e_max_kV_cm'))
    record = EvidenceRecord(record_id='delta-only',formula_raw='BaTiO3',
                            e_reported_kind='delta',e_reported_kV_cm=float(value))
    counts = condition_summary([record],Goals(**g).request(),'synthetic-test')
    assert counts['field_matches'] == 1
    assert record.e_start_kV_cm is None and record.e_end_kV_cm is None
    if 'field_protocol' in p:
        assert p['field_protocol']['kind']=='unknown'
        assert all(p['field_protocol'][k] is None for k in ('start_kV_cm','end_kV_cm','peak_kV_cm','measurement_branch'))


@pytest.mark.parametrize('value', ['0','-1','nan','inf','1e309','abc'])
def test_invalid_delta_is_rejected(value):
    with pytest.raises(ValueError):
        prepare_inputs({'e_delta':value})


def test_empty_delta_and_advanced_units_are_independent():
    assert prepare_inputs({'e_delta':''})['goals']==Goals().model_dump()
    p = prepare_inputs({'e_delta':'12.5','e_delta_unit':'kV/cm','field_unit':'kV/mm','e_max':'2'})
    assert p['goals']['delta_E_kV_cm']==12.5 and p['goals']['e_max_kV_cm']==20
    assert p['goals']['e_start_kV_cm'] is None


@pytest.mark.parametrize('text,key,value', [
    ('最大场强不超过30','e_max_kV_cm',30),
    ('最大电场不超过 3 kV/mm','e_max_kV_cm',30),
    ('从0加到30','e_end_kV_cm',30),
    ('从 30 降到 0 kV/cm','e_start_kV_cm',30),
])
def test_explicit_natural_field_semantics(text,key,value):
    p=prepare_inputs({'natural_language':text,'e_delta':'30'})
    assert p['goals'][key]==value
    assert p['raw']['natural_language']==text
    if '从' in text:
        assert p['goals']['e_start_kV_cm'] is not None and p['goals']['e_end_kV_cm'] is not None
    else:
        assert p['goals']['e_start_kV_cm'] is None


@pytest.mark.parametrize('raw', [
    {'natural_language':'最大场强不超过30','e_max':'40'},
    {'natural_language':'从0加到30','e_start':'10','e_end':'40'},
    {'natural_language':'从0加到30','e_delta':'20'},
])
def test_conflicts_require_correction_and_confirmation(raw):
    with pytest.raises(ValueError,match='冲突'):
        prepare_inputs(raw)


def test_form_delta_review_and_edit(tmp_path,monkeypatch):
    import ecalx_local
    if hasattr(ecalx_local,'STORAGE'):
        monkeypatch.setattr(ecalx_local,'STORAGE',tmp_path/'tasks')
    monkeypatch.setenv('ECALX_LOCAL_STORAGE',str(tmp_path/'tasks'))
    monkeypatch.setattr('ecalx_local.ui_home.launch',lambda *a,**k:None)
    monkeypatch.setattr('urllib.request.build_opener',lambda *a,**k:pytest.fail('must stay offline'))
    app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=25).run()
    assert not app.exception
    assert app.text_input(key='home_edelta').value==''
    assert not any(s.label=='电场条件含义' for s in app.selectbox)
    assert next(e for e in app.expander if e.label=='高级电场条件').proto.expanded is False
    app.text_input(key='home_edelta').set_value('12.5')
    next(b for b in app.button if b.label=='检查研究条件').click().run()
    assert not app.exception
    summary=app.session_state['prepared']['summary']
    assert summary[:3]==['温度范围：未指定 至 未指定','电场变化幅度 ΔE：12.5 kV/cm','排除元素：未指定']
    assert not any('起点' in s or '终点' in s for s in summary)
    assert next(b for b in app.button if b.label=='开始研究').disabled
    next(b for b in app.button if b.label=='返回修改研究条件').click().run()
    assert app.text_input(key='home_edelta').value=='12.5'
    app.text_area(key='home_question').set_value('最大场强不超过30')
    app.text_input(key='home_emax').set_value('40')
    next(b for b in app.button if b.label=='检查研究条件').click().run()
    assert app.error and 'prepared' not in app.session_state
    assert '请确认' in app.error[0].value
    assert not any(b.label=='开始研究' for b in app.button)


def test_delta_only_task_and_child_leave_history_untouched(tmp_path,monkeypatch):
    monkeypatch.setattr('urllib.request.build_opener',lambda *a,**k:pytest.fail('must stay offline'))
    p=prepare_inputs({'e_delta':'20'})
    agent=LocalAgent(tmp_path/'tasks'); client=LocalWorkflow()
    t=create_research(p,'local',confirmed=True,storage=tmp_path/'tasks')
    agent.advance(t['id'],client);agent.confirm_plan(t['id'],confirmed=True,accept_unquantified=True)
    done=agent.run_until_wait(t['id'],client)
    assert done['status']=='COMPLETED',done.get('error')
    before=json.dumps(agent.get(t['id']),sort_keys=True)
    from ecalx_local.conversation import prepare_child
    child=prepare_child(done,'调整 ΔE 为30',{**done['frozen_goals'],'delta_E_kV_cm':30})
    new=create_research(child,'local',confirmed=True,storage=tmp_path/'tasks')
    assert new['id']!=t['id'] and new['frozen_goals']['delta_E_kV_cm']==30
    assert json.dumps(agent.get(t['id']),sort_keys=True)==before


def render_child(task, storage):
    import streamlit as st
    from ecalx_local.conversation_ui import child_editor
    if 'research_change_draft' not in st.session_state:
        st.session_state.research_change_draft = {
            'parent_task_id':task['id'], 'question':'调整 ΔE 为30',
            'goals':{**task['frozen_goals'],'delta_E_kV_cm':30}, 'notes':[]}
    child_editor(task, storage)


def test_child_form_positive_or_empty_delta(tmp_path):
    task={'id':'task-'+'a'*32,'frozen_goals':Goals(delta_E_kV_cm=20).model_dump()}
    before=json.dumps(task,sort_keys=True)
    app=AppTest.from_function(render_child,args=(task,str(tmp_path))).run()
    assert not app.exception
    field=next(t for t in app.text_input if t.label=='电场变化幅度 ΔE（kV/cm）')
    field.set_value('0')
    next(b for b in app.button if b.label=='核对新任务条件').click().run()
    assert app.error and '必须为正数' in app.error[0].value
    assert not any(b.label=='创建新的研究任务' for b in app.button)
    next(t for t in app.text_input if t.label=='电场变化幅度 ΔE（kV/cm）').set_value('')
    next(b for b in app.button if b.label=='核对新任务条件').click().run()
    assert not app.exception and not app.error
    assert next(b for b in app.button if b.label=='创建新的研究任务').disabled
    assert json.dumps(task,sort_keys=True)==before
