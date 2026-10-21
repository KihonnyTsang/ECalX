"""Offline usage admission and legacy compatibility; network is globally blocked."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from ecalx.agent.llm import ProviderError
from ecalx_local import byo_policy
from ecalx_local.configuration import (DEFAULT_LIMITS, config_dir, read_config,
    save_config, configured_budget)
from ecalx_local.engine import LocalAgent, LiveClient, online_quote
from ecalx_local.inputs import prepare_inputs
from ecalx_local.jobs import create_research
from test_http_contract import configured
from test_byo import complete, import_example


def seed(task='historical', *, count=1, tokens=100, known=1, day=None):
    policy=byo_policy.BYOPolicy(task, lambda raw: None)
    day=day or datetime.now(timezone.utc).date().isoformat()
    with policy.connect() as db:
        for i in range(count):
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?)',
                (task+str(i),task,day,known,tokens,0.005,'old-sha','RECEIVED',
                 json.dumps({'input_per_million':1,'output_per_million':2}),
                 'deepseek-ai/DeepSeek-V4-Flash'))


def rows():
    with sqlite3.connect(byo_policy.ledger_path()) as db:
        return db.execute('SELECT * FROM requests ORDER BY id').fetchall()


def test_legacy_config_key_data_and_history_unchanged(isolation, tmp_path):
    configured()
    legacy={'model':read_config()['model'], 'prices':{'input_per_million':1,'output_per_million':2},
            'daily_cny':0.01}
    settings=config_dir()/'settings.json'
    settings.write_text(json.dumps(legacy))
    seed()
    old_rows=rows()
    import_example(isolation)
    agent=LocalAgent(tmp_path/'tasks'); task=complete(agent)
    key=config_dir()/'.env'
    paths=[key,settings]+[p for root in (isolation,agent.storage_root) for p in root.rglob('*') if p.is_file()]
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert read_config()['usage_limits']==DEFAULT_LIMITS
    assert online_quote()['max_requests']==10  # Tiny obsolete CNY cap cannot block.
    assert {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}==before
    save_config({'model':legacy['model']})
    assert read_config()['prices']==legacy['prices'] and read_config()['daily_cny']==0.01
    assert rows()==old_rows
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in before.items() if p!=settings)
    assert key.stat().st_mode & 0o777==0o600
    assert json.dumps(agent.get(task['id']),sort_keys=True)==json.dumps(task,sort_keys=True)


@pytest.mark.parametrize('limit', ['daily_requests','daily_tokens','task_requests','task_tokens'])
def test_each_limit_refuses_before_http(isolation,tmp_path,monkeypatch,limit):
    import ecalx.agent.siliconflow as transport
    configured()
    q=online_quote()
    task=create_research(prepare_inputs({}),'live',confirmed=True,consent=q,storage=tmp_path/'tasks')
    agent=LocalAgent(tmp_path/'tasks');client=LiveClient();client.bind(agent,task['id'])
    owner=task['id'] if limit.startswith('task') else 'historical'
    cfg=read_config()['usage_limits']
    # Add shared usage after confirmation, so the per-send gate must enforce it.
    if limit.endswith('requests'): seed(owner,count=cfg[limit],tokens=1)
    else: seed(owner,tokens=cfg[limit])
    before=rows()
    def forbidden(*args,**kwargs): pytest.fail('A capped request reached HTTP')
    monkeypatch.setattr(transport.urllib.request,'build_opener',forbidden)
    failed=agent.advance(task['id'],client)
    assert failed['status']=='FAILED'
    assert failed['provider_responses'][-1]['metrics']['requests']==0
    assert failed['provider_responses'][-1]['error']['code']=='BUDGET_LIMIT'
    assert rows()==before


@pytest.mark.parametrize('limit',['daily_requests','daily_tokens'])
def test_research_and_followup_preflight_share_daily_limits(isolation,limit):
    from ecalx_local.conversation_live import followup_quote
    configured();seed(count=DEFAULT_LIMITS[limit] if limit.endswith('requests') else 1,
                      tokens=1 if limit.endswith('requests') else DEFAULT_LIMITS[limit])
    for quote in (online_quote,followup_quote):
        with pytest.raises(ProviderError) as error: quote()
        assert error.value.code=='BUDGET_LIMIT'


def test_followup_uses_own_budget_and_blocks_at_send(isolation,tmp_path,monkeypatch):
    from ecalx_local.conversation import Conversation
    from ecalx_local.conversation_live import followup_quote,FollowupClient
    import ecalx.agent.siliconflow as transport
    configured()
    # Enough daily quota for follow-up, insufficient for a fresh research task.
    seed(tokens=DEFAULT_LIMITS['daily_tokens']-65536)
    with pytest.raises(ProviderError): online_quote()
    q=followup_quote()
    assert q['max_tokens']==65536
    agent=LocalAgent(tmp_path/'tasks');task=complete(agent)
    conv=Conversation(agent.storage_root)
    with pytest.raises(ValueError): conv.create(task['id'],'总结依据',mode='live',consent=None)
    turn=conv.create(task['id'],'总结依据',mode='live',consent=q)
    seed('other',tokens=65536)
    before=rows()
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *_:pytest.fail('Capped follow-up reached HTTP'))
    result=conv.run(task['id'],turn['id'],FollowupClient())
    assert result['status']=='FAILED' and result['paid_api_requests']==0
    assert rows()==before
    assert agent.get(task['id'])==task


def test_historical_unknown_usage_blocks_every_day(isolation):
    from ecalx_local.conversation_live import followup_quote
    configured();seed(known=0,day='2000-01-01')
    before=rows()
    for quote in (online_quote,followup_quote):
        with pytest.raises(ProviderError) as error: quote()
        assert error.value.code=='UNKNOWN_USAGE'
    client=LiveClient()
    with pytest.raises(ProviderError) as error:
        byo_policy.BYOPolicy('new',lambda raw:None).before_send(client,b'{}',60)
    assert error.value.code=='UNKNOWN_USAGE' and rows()==before


def test_advanced_limits_reach_task_and_followup(isolation,tmp_path):
    from ecalx_local.conversation_live import followup_quote,FollowupClient
    configured()
    limits={**DEFAULT_LIMITS,'task_requests':3,'task_tokens':50000,'task_tool_calls':4,
            'output_tokens':512,'request_seconds':20,'task_seconds':100}
    save_config({'model':read_config()['model'],'usage_limits':limits})
    q=online_quote();task=create_research(prepare_inputs({}),'live',confirmed=True,consent=q,storage=tmp_path/'tasks')
    budget=configured_budget()
    assert all(task['limits'][k]==v for k,v in budget.model_dump().items())
    assert LiveClient().budget==budget
    follow=FollowupClient()
    assert follow.budget==configured_budget(followup=True)
    assert followup_quote()['max_tokens']==50000
    # Config changes invalidate prior human consent instead of expanding it.
    save_config({'model':read_config()['model'],'usage_limits':{**limits,'task_tokens':60000}})
    agent=LocalAgent(tmp_path/'tasks');client=LiveClient(budget=budget);client.bind(agent,task['id'])
    assert agent.advance(task['id'],client)['status']=='FAILED'
    assert not byo_policy.ledger_path().exists()


@pytest.mark.parametrize('field,value',[('daily_requests',0),('daily_tokens',True),
    ('task_requests',49),('task_tokens',1000001),('task_tool_calls',65),
    ('output_tokens',4097),('request_seconds',181),('task_seconds',601)])
def test_invalid_adjustment_cannot_overwrite_config(isolation,field,value):
    configured();before=(config_dir()/'settings.json').read_bytes()
    with pytest.raises(ValueError):
        save_config({'model':read_config()['model'],'usage_limits':{field:value}})
    assert (config_dir()/'settings.json').read_bytes()==before


@pytest.mark.parametrize('usage',[{},
    {'prompt_tokens':100,'completion_tokens':10},
    {'prompt_tokens':100,'completion_tokens':10,'total_tokens':109},
    {'prompt_tokens':True,'completion_tokens':10,'total_tokens':11},
    {'prompt_tokens':999999,'completion_tokens':1,'total_tokens':1000000}])
def test_response_unknown_or_excess_usage_blocks_next_request(isolation,tmp_path,monkeypatch,usage):
    import ecalx.agent.siliconflow as transport
    from ecalx_local.conversation_live import followup_quote
    configured();q=online_quote()
    task=create_research(prepare_inputs({}),'live',confirmed=True,consent=q,storage=tmp_path/'tasks')
    agent=LocalAgent(tmp_path/'tasks');client=LiveClient();client.bind(agent,task['id'])
    sends=[]
    class Response:
        status=200;headers={}
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,*_):
            return json.dumps({'model':client.model,'usage':usage,
                'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                    'tool_calls':[{'id':'unknown-test','type':'function','function':{
                        'name':'submit_research_plan','arguments':json.dumps({
                            'steps':['search_material_evidence','generate_research_report'],'clarification':None})}}]}}]}).encode()
    class Opener:
        def open(self,request,**_):
            sends.append(request)
            assert byo_policy.usage_status()['unknown']==1  # Persisted before HTTP.
            return Response()
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *_:Opener())
    agent.advance(task['id'],client)
    assert len(sends)==1 and byo_policy.usage_status()['unknown']==1
    before=rows()
    for quote in (online_quote,followup_quote):
        with pytest.raises(ProviderError) as error:quote()
        assert error.value.code=='UNKNOWN_USAGE'
    with pytest.raises(ProviderError) as error:
        byo_policy.BYOPolicy('another-task',lambda raw:None).before_send(client,b'{}',60)
    assert error.value.code=='UNKNOWN_USAGE' and rows()==before and len(sends)==1


@pytest.mark.parametrize('status',[429,503])
def test_http_errors_never_retry(isolation,tmp_path,monkeypatch,status):
    import urllib.error
    import ecalx.agent.siliconflow as transport
    configured();q=online_quote()
    task=create_research(prepare_inputs({}),'live',confirmed=True,consent=q,storage=tmp_path/'tasks')
    agent=LocalAgent(tmp_path/'tasks');client=LiveClient();client.bind(agent,task['id'])
    sends=[]
    class Opener:
        def open(self,request,**_):
            sends.append(request)
            raise urllib.error.HTTPError(request.full_url,status,'TEST_ONLY',{},None)
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *_:Opener())
    assert agent.advance(task['id'],client)['status']=='FAILED'
    assert len(sends)==1 and byo_policy.usage_status()['unknown']==1


def test_worker_cannot_send_without_consent(isolation,tmp_path,monkeypatch):
    import ecalx.agent.siliconflow as transport
    from ecalx_local.worker import run
    configured();q=online_quote()
    task=create_research(prepare_inputs({}),'live',confirmed=True,consent=q,storage=tmp_path/'tasks')
    agent=LocalAgent(tmp_path/'tasks')
    task['online_consent']=None;agent.save(task)
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *_:pytest.fail('Unconfirmed worker reached HTTP'))
    run(task['id'],agent.storage_root)
    assert agent.get(task['id'])['status']=='FAILED'
    assert not byo_policy.ledger_path().exists()


def test_readonly_preflight_requires_no_prices_or_key_load(isolation):
    from ecalx_local.preflight import readonly_preflight
    # No config/key still exposes the offline tools without creating a ledger.
    result=readonly_preflight(prepare_inputs({})['goals'])
    assert result['status']=='OFFLINE_READY' and result['network_requests']==0
    configured()
    result=readonly_preflight(prepare_inputs({})['goals'])
    assert result['status']=='CONFIGURED_NOT_VERIFIED' and result['network_requests']==0
    assert '实际费用请以供应商账单为准' in ' '.join(result['checks'])
    assert 'prices' not in result['quote'] and 'max_estimated_cny' not in result['quote']
    assert not byo_policy.ledger_path().exists()
