from copy import deepcopy
from pathlib import Path
import pytest
from ecalx_local.inputs import prepare_inputs
from ecalx_local.element_research import generate_from_elements,create_simulation,advance_simulation,ranking
from ecalx_local.engine import LocalAgent
from ecalx.materials.chemistry import parse_formula
from ecalx.agent.tools import ReadOnlyEvidence
from ecalx.agent.contracts import Goals
from ecalx.experiments.candidate_pool import generated_candidate_pool


def test_element_semantics():
    p=prepare_inputs({'allowed_elements':'Ba、Sr、Ca、Ti、O','exclude':'Pb'})
    assert p['allowed_elements']==['Ba','Sr','Ca','Ti','O']
    assert p['goals']['include_elements']==[]
    assert p['goals']['exclude_elements']==['Pb']
    for key in ('temperature_min_K','temperature_max_K','delta_E_kV_cm','delta_t_K','material_type'):
        assert p['goals'][key] is None
    with pytest.raises(ValueError): prepare_inputs({'allowed_elements':'Ba,Pb','exclude':'Pb'})
    with pytest.raises(ValueError): prepare_inputs({'allowed_elements':'Xx'})
    assert prepare_inputs({})['allowed_elements']==[]


def test_generated_pool_and_real_m4_execution(tmp_path,isolation):
    # Explicit TEST_ONLY seed; results here never enter the demonstration workspace.
    from ecalx.data.schema import EvidenceRecord
    record=EvidenceRecord(record_id='TEST_ONLY_seed',formula_raw='BaTiO3',
        url='https://example.invalid/SYNTHETIC_TEST_DATA',original_row='TEST_ONLY')
    class TestRepository:
        data_version='TEST_ONLY'
        def records(self): return [record]
    repo=TestRepository()
    prepared=prepare_inputs({'allowed_elements':'Ba Sr Ca Ti O','exclude':'Pb'})
    generated=generate_from_elements(Goals(**prepared['goals']).request(),repo,prepared['allowed_elements'])
    assert len(generated['candidates'])==10
    assert len({c['candidate_id'] for c in generated['candidates']})==10
    for c in generated['candidates']:
        assert set(parse_formula(c['formula_raw']))<=set(prepared['allowed_elements'])
        assert c['status']=='generated_unverified' and c['predictions']==[]
        assert 'SYNTHETIC_TEST_DATA' in c['generation_sources'][0]['seed_sources'][0]['url']
    bridge=generated_candidate_pool(generated,'SIMULATED',tmp_path)
    assert len(bridge['accepted'])==5 and len(bridge['rejected'])==5
    assert all('Ca' in next(c['formula_raw'] for c in generated['candidates'] if c['candidate_id']==r['generated_candidate_id']) for r in bridge['rejected'])
    # Use the actual persisted agent and experiment stores, no fake simulation output.
    from ecalx_local.engine import LocalWorkflow
    agent=LocalAgent(tmp_path/'tasks')
    task=agent.create_task('探索电卡材料',LocalWorkflow())
    task['input_snapshot']=prepared
    task['calls']=[{'name':'generate_material_candidates','output':{'status':'OK','data':generated}}]
    agent.save(task)
    original=deepcopy(task['frozen_goals'])
    run=create_simulation(agent,task,303.,0.,20.)
    initial=ranking(run)
    assert len(initial)==2
    run=advance_simulation(agent,task,run)
    assert len(ranking(run))==3 and run['report']['loop']['completed_rounds']==1
    run=advance_simulation(agent,task,run,all_remaining=True)
    assert len(ranking(run))==5 and run['report']['loop']['completed_rounds']==3
    scores=[r['模拟评分'] for r in ranking(run)]
    assert scores==sorted(scores,reverse=True)
    assert all(o['provenance']=='SIMULATED' for o in run['report']['observations'])
    assert all(m['independent_test']['metrics'] is None for m in run['report']['models'])
    assert agent.get(task['id'])['frozen_goals']==original
    assert agent.get(task['id'])['input_snapshot']['goals']==prepared['goals']
    # No-op when exhausted; no duplicate observations or extra rounds.
    again=advance_simulation(agent,task,run,all_remaining=True)
    assert len(again['report']['observations'])==5
    assert again['report']['loop']['completed_rounds']==3
    changed=create_simulation(agent,task,293.,0.,20.)
    baseline=ranking(changed)[0]['模拟评分']
    changed=advance_simulation(agent,task,changed,all_remaining=True)
    assert ranking(changed)[0]['模拟评分']>baseline
    assert len(agent.get(task['id'])['simulation_runs'])==2
    assert agent.get(task['id'])['frozen_goals']==original


def test_empty_and_other_registered_family():
    from ecalx.data.schema import EvidenceRecord
    class TestRepository:
        data_version='TEST_ONLY'
        def records(self): return [EvidenceRecord(record_id='TEST_ONLY_Ce',formula_raw='CeO2',
            url='https://example.invalid/SYNTHETIC_TEST_DATA')]
    request=Goals().request()
    result=generate_from_elements(request,TestRepository(),['Ce','Zr','O'])
    assert len(result['candidates'])==5
    assert all(c['family_rule']=='fluorite_AO2' for c in result['candidates'])
    assert not generate_from_elements(request,TestRepository(),['Ba','Ti','O'])['candidates']


def test_known_composition_is_not_claimed_new():
    from ecalx.data.schema import EvidenceRecord
    class TestRepository:
        data_version='TEST_ONLY'
        def records(self):
            return [EvidenceRecord(record_id='TEST_ONLY_seed',formula_raw='BaTiO3',url='https://example.invalid/SYNTHETIC_TEST_DATA/a'),
                    EvidenceRecord(record_id='TEST_ONLY_known',formula_raw='Ba0.95Sr0.05TiO3',url='https://example.invalid/SYNTHETIC_TEST_DATA/b')]
    result=generate_from_elements(Goals().request(),TestRepository(),['Ba','Sr','O','Ti'])
    known=[c for c in result['candidates'] if c['database_presence']=='present']
    assert len(known)==1 and known[0]['known_database_matches'][0]['record_id']=='TEST_ONLY_known'
    assert known[0]['status']=='generated_unverified' and known[0]['predictions']==[]
