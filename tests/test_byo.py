from pathlib import Path
from io import BytesIO
import json
import os
import sqlite3
import pytest
from ecalx_local.import_data import preview,commit,FIELDS
from ecalx_local.engine import LocalAgent,LocalWorkflow,BUDGET
from ecalx_local.inputs import prepare_inputs
from ecalx_local.conversation import Conversation,fingerprint
from ecalx_local.presentation import report_markdown,candidate_cards,evidence_rows
from ecalx.data.repository import EvidenceRepository

EXAMPLE=Path(__file__).parents[1]/'examples/SYNTHETIC_TEST_DATA.csv'

def import_example(workspace):
    content=EXAMPLE.read_bytes()
    import pandas as pd
    cols=pd.read_csv(EXAMPLE).columns
    result=preview(content,EXAMPLE.name,{k:k for k in FIELDS if k in cols})
    assert result['errors']==[],result['errors']
    return result,commit(content,result,workspace)

def complete(agent):
    tid=agent.create_task('探索用户数据中的候选组成',LocalWorkflow(),BUDGET)['id']
    agent.establish_goals(tid,prepare_inputs({'natural_language':'探索候选组成'})['goals'],confirmed=True)
    agent.advance(tid,LocalWorkflow())
    agent.confirm_plan(tid,confirmed=True)
    task=agent.run_until_wait(tid,LocalWorkflow())
    assert task['status']=='COMPLETED',task.get('error')
    return task

def test_real_tools_import_report_followup(isolation,tmp_path,monkeypatch):
    result,log=import_example(isolation)
    assert log['inserted']==3 and log['predictions_enabled'] is False
    assert result['records'][0].temperature_K==298.15
    assert result['records'][0].e_end_kV_cm==20
    assert result['records'][0].e_max_kV_cm==30
    assert result['records'][1].e_start_kV_cm is None
    assert result['records'][2].temperature_K is None
    assert result['records'][0].delta_T_ad_K is None
    assert result['records'][0].raw_metadata_json['temperature_unit']=='°C'
    assert commit(EXAMPLE.read_bytes(),result,isolation)['already_imported']
    agent=LocalAgent(tmp_path/'tasks');task=complete(agent)
    generated=[c for c in task['calls'] if c['name']=='generate_material_candidates'][0]['output']['data']['candidates']
    assert generated and all(c['status']=='generated_unverified' for c in generated)
    checks=[c for c in task['calls'] if c['name']=='validate_chemical_constraints']
    assert len(checks)==len(generated) and all(c['output']['data']['chemical_pass'] for c in checks)
    assert task['report']['faithfulness']['passed']
    report=report_markdown(task)
    assert 'unknown' in report and 'SYNTHETIC_TEST_DATA' in report
    assert len(candidate_cards(task))==len(generated)
    assert len(evidence_rows(task))==3
    original=fingerprint(task)
    conv=Conversation(agent.storage_root)
    ids=[c['candidate_id'] for c in generated[:2]]
    turn=conv.create(task['id'],'比较两个候选的依据',selected=ids)
    assert turn['status']=='COMPLETED' and 'comparison' in turn['block_ids']
    again=conv.create(task['id'],'它们还缺哪些证据？')
    assert again['status']=='COMPLETED' and again['selected_candidates']==ids
    change=conv.create(task['id'],'把电场上限改为 5 kV/cm')
    assert change['status']=='NEEDS_NEW_TASK'
    assert fingerprint(agent.get(task['id']))==original
    # New import cannot change the old snapshot or report/followup.
    more=b'formula,url\nCaTiO3,https://example.invalid/SYNTHETIC_TEST_DATA/new\n'
    p=preview(more,'new.csv',{'formula':'formula','url':'url'});commit(more,p,isolation)
    assert len(EvidenceRepository(isolation/'data/evidence.sqlite3').records())==4
    assert len(EvidenceRepository(task['evidence_snapshot']).records())==3
    assert report_markdown(agent.get(task['id']))==report
    assert not (isolation/'api_usage.sqlite3').exists()

@pytest.mark.parametrize('line',[
 'BaTiO3,abc,K,0,2,2,kV/mm',
 'BaTiO3,25,,0,2,2,kV/mm',
 'BaTiO3,25,°C,0,2,1,kV/mm',
 'BaTiO3,25,°C,0,2,2,wrong',
 'NOT_A_FORMULA,25,°C,0,2,2,kV/mm',
 'BaTiO3,nan,K,0,2,2,kV/mm',
])
def test_bad_import_atomic(isolation,line):
    raw=('formula,temperature,temperature_unit,e_start,e_end,e_delta,field_unit\n'+line+'\n').encode()
    result=preview(raw,'user.csv',{k:k for k in FIELDS if k in raw.decode().splitlines()[0].split(',')})
    assert result['errors']
    with pytest.raises(ValueError):commit(raw,result,isolation)
    assert not (isolation/'raw').exists()

def test_excel_labels_and_protocol_separation(isolation):
    import pandas as pd
    data=pd.DataFrame({'f':['BaTiO3','SrTiO3','CaTiO3'],'t':['25','','30'],
        'm':['direct','indirect','direct'],'dt':['1','','2'],'ds':['','0.1',''],
        'p':['fast','slow','slow'],'doi':['10.1234/user-supplied-a','10.1234/user-supplied-b','10.1234/user-supplied-c']})
    out=BytesIO();data.to_excel(out,index=False,sheet_name='user')
    p=preview(out.getvalue(),'user.xlsx',{'formula':'f','temperature':'t','method':'m','delta_t':'dt','delta_s':'ds','protocol':'p','doi':'doi'},
        units={'temperature_unit':'°C','delta_t_unit':'K','delta_s_unit':'J/(g K)'},sheet='user')
    assert p['errors']==[]
    assert p['records'][1].delta_T_ad_K is None and p['records'][1].delta_S_J_kgK==100
    assert p['records'][1].temperature_K is None
    from ecalx.services.discovery import comparable_key
    assert comparable_key(p['records'][0])!=comparable_key(p['records'][2])
    assert all('sign_convention_unverified' in r.quality_flags for r in p['records'])
    commit(out.getvalue(),p,isolation)

def test_empty_and_missing_sources_no_fabrication(isolation,tmp_path):
    agent=LocalAgent(tmp_path/'tasks');task=complete(agent)
    assert not candidate_cards(task) and not evidence_rows(task)
    raw=b'formula\nBaTiO3\nSrTiO3\n'
    p=preview(raw,'no-sources.csv',{'formula':'formula'});commit(raw,p,isolation)
    task=complete(LocalAgent(tmp_path/'other'))
    assert not candidate_cards(task) and len(evidence_rows(task))==2

def test_key_permissions_and_redaction(isolation,tmp_path,monkeypatch):
    from ecalx_local.configuration import save_config,config_dir,load_credentials
    from ecalx.agent.llm import redact
    secret='sk-BYO_FAKE_KEY_NEVER_SENT'
    cfg={'model':'deepseek-ai/DeepSeek-V4-Flash','prices':{'input_per_million':1,'output_per_million':2},'daily_cny':5}
    save_config(cfg,secret)
    assert config_dir().stat().st_mode&0o777==0o700
    assert (config_dir()/'.env').stat().st_mode&0o777==0o600
    assert secret not in (config_dir()/'settings.json').read_text()
    load_credentials()
    assert redact(secret)=='[REDACTED]'
    agent=LocalAgent(tmp_path/'tasks')
    task=agent.create_task('不能保存 '+secret,LocalWorkflow())
    assert secret not in json.dumps(agent.get(task['id']))
    (config_dir()/'.env').chmod(0o644)
    monkeypatch.delenv('SILICONFLOW_API_KEY')
    with pytest.raises(Exception):load_credentials()
