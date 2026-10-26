"""Regressions for the large-result freeze and receipt-backed result cards."""
from copy import deepcopy
import json
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from ecalx_local.engine import LocalAgent, LocalWorkflow
from ecalx_local.inputs import prepare_inputs
from ecalx_local.element_research import (
    generate_from_elements, create_simulation, advance_simulation, ranking,
)
from ecalx.agent.contracts import Goals
from ecalx.data.schema import EvidenceRecord


def generated_task(tmp_path):
    class Repository:
        data_version='TEST_ONLY'
        def records(self):
            return [EvidenceRecord(record_id='TEST_ONLY_seed',formula_raw='BaTiO3',
                url='https://example.invalid/SYNTHETIC_TEST_DATA')]
    prepared=prepare_inputs({'allowed_elements':'Ba Sr Ca Ti O','exclude':'Pb'})
    generated=generate_from_elements(Goals(**prepared['goals']).request(),Repository(),prepared['allowed_elements'])
    agent=LocalAgent(tmp_path/'tasks')
    task=agent.create_task('TEST_ONLY performance regression',LocalWorkflow())
    task.update(status='COMPLETED',input_snapshot=prepared,
        calls=[{'id':'TEST_ONLY_generate','name':'generate_material_candidates',
                'output':{'status':'OK','data':generated}}])
    agent.save(task)
    return agent,task


def test_history_is_bounded_metadata_and_readers_do_not_take_write_lock(tmp_path):
    agent,task=generated_task(tmp_path)
    task['large_unused_receipt']='x'*2_000_000
    agent.save(task)
    for i in range(14):
        agent.create_task(f'TEST_ONLY history {i}',LocalWorkflow())
    # A background worker can hold the writer reservation without blocking UI reads.
    with sqlite3.connect(agent.path) as writer:
        writer.execute('BEGIN IMMEDIATE')
        page,total=agent.task_page(limit=12)
        assert total==15 and len(page)==12
        last,_=agent.task_page(limit=12,offset=12)
        assert len(last)==3 and not {r['id'] for r in page}&{r['id'] for r in last}
        assert all('calls' not in r and 'large_unused_receipt' not in r for r in page+last)
        assert len(json.dumps(page+last))<20_000
        assert agent.get(task['id'])['large_unused_receipt']==task['large_unused_receipt']
        assert agent.has_task(task['id']) and not agent.has_task('missing')


def test_default_results_skip_exports_models_and_hidden_tabs(tmp_path,monkeypatch):
    agent,task=generated_task(tmp_path)
    task['large_unused_receipt']='x'*2_000_000
    agent.save(task)
    import ecalx_local.ui_research as ui
    import ecalx_local.element_ui as elements
    def forbidden(*args,**kwargs):
        raise AssertionError('Hidden details/computation must not execute on result render')
    for module,name in ((ui,'downloads'),(elements,'checks_panel'),
                        (elements,'simulation_panel'),(elements,'optimization_panel'),
                        (elements,'report_overview')):
        monkeypatch.setattr(module,name,forbidden)
    from ecalx.data.repository import EvidenceRepository
    monkeypatch.setattr(EvidenceRepository,'records',forbidden)
    app=AppTest.from_string('''import streamlit as st
from ecalx_local.engine import LocalAgent
from ecalx_local.ui_research import research
st.session_state.task_id='''+repr(task['id'])+'''
research(LocalAgent('''+repr(str(agent.storage_root))+'''),None,lambda *args:None)
''').run(timeout=15)
    assert not app.exception
    assert len(app.json)==0
    assert sum('class="formula"' in m.value for m in app.markdown)==4


def test_cards_show_actual_observed_scores_and_no_fabricated_delta_t(tmp_path):
    agent,task=generated_task(tmp_path)
    run=create_simulation(agent,task,293.,0.,20.)
    run=advance_simulation(agent,task,run,all_remaining=True)
    task=agent.get(task['id'])
    rows=ranking(run)
    app=AppTest.from_string('''from ecalx_local.engine import LocalAgent
from ecalx_local.presentation import candidate_cards
from ecalx_local.ui_research import candidates
a=LocalAgent('''+repr(str(agent.storage_root))+''')
t=a.get('''+repr(task['id'])+''')
candidates(t,candidate_cards(t))
''').run(timeout=15)
    assert not app.exception
    assert [m.value for m in app.metric]==[f"{r['模拟评分']:.3f}" for r in rows[:4]]
    assert all(m.label=='模拟评分' for m in app.metric)
    assert sum('模拟排名 #1' in m.value for m in app.markdown)==1
    assert all(c['status']=='generated_unverified' for c in task['calls'][0]['output']['data']['candidates'])
    # Rendering and changing pages must not execute another query or mutate history.
    before=deepcopy(task)
    app.number_input[0].set_value(2).run()
    assert [m.value for m in app.metric]==[f"{rows[4]['模拟评分']:.3f}"]
    app.number_input[0].set_value(3).run()
    assert len(app.metric)==0
    assert agent.get(task['id'])==before


def test_simulation_does_not_override_confirmed_conditions(tmp_path):
    agent,task=generated_task(tmp_path)
    task['frozen_goals'].update(temperature_min_K=310.,temperature_max_K=320.,
                              delta_E_kV_cm=10.)
    with pytest.raises(ValueError,match='模拟协议与已确认目标冲突'):
        create_simulation(agent,task,293.,0.,20.)
    assert not agent.get(task['id']).get('simulation_runs')
