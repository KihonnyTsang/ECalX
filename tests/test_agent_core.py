"""Production runtime/HTTP policy under synthetic transport and tool boundaries.

TEST_ONLY software results, temporary SQLite only, no historical data or API sends.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import sqlite3
from zoneinfo import ZoneInfo
import urllib.error
import pytest
from ecalx.agent.runtime import ResearchAgent
from ecalx.agent.siliconflow import SiliconFlowChatCompletionsClient, chat_messages, NoRedirect
from ecalx.agent.config import MODEL, BASE_URL
from ecalx.agent.llm import Reply, ScriptedClient, ProviderError, configured_client
from ecalx.agent.contracts import Goals, VirtualPlan, definitions
from ecalx.agent.planning import accept_proposal
from ecalx.agent.api_policy import ProjectAPIPolicy, initialize_ledger, CAPS, settings
from ecalx.agent.faithfulness import audit_report
from ecalx.experiments.contracts import ExperimentError

pytestmark = pytest.mark.task_store


def call(name='search_material_evidence', args=None, cid='TEST_ONLY_CALL'):
    return {'id':cid, 'name':name, 'arguments':args or {}}


def wire_call(c):
    return {'id':c['id'],'type':'function','function':{'name':c['name'],'arguments':json.dumps(c['arguments'])}}


def body(calls):
    return {'model':MODEL,'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120},
            'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                        'tool_calls':[wire_call(c) for c in calls]}}]}


class TestOnlyTools:
    """Explicit fake: no chemistry, evidence repository, experiment or training."""
    __test__ = False
    def __init__(self): self.executed=[]
    def preview(self,name,args,task,key=None):
        return {'budget':{'total_queries':1}, 'test_double':'TEST_ONLY'}
    def execute(self,name,args,task,key):
        task['checkpoint']()
        self.executed.append(name)
        return {'test_double':'TEST_ONLY', 'data_version':'TEST_ONLY_VERSION',
                'evidence':[{'record_id':'TEST_ONLY_SOURCE','url':'https://example.invalid/TEST_ONLY',
                             'source_sha256':'0'*64,'measurement_method':'TEST_ONLY'}]}


@pytest.fixture
def agent(tmp_path):
    return ResearchAgent(tmp_path, storage_root=tmp_path/'new_tasks', tools=TestOnlyTools())


def start(agent, client, steps=None):
    t=agent.create_task('',client)
    t=agent.advance(t['id'],client)
    assert t['status']=='DRAFT' and t['plan']['goals']==Goals().model_dump()
    return agent.confirm_plan(t['id'],confirmed=True)['id']


@pytest.fixture
def transport(tmp_path, monkeypatch):
    """Use real policy reservation/finalization; replace only HTTP opener."""
    root=tmp_path/'isolated_policy'; (root/'config').mkdir(parents=True)
    grant={'provider':'siliconflow','model':MODEL,'endpoint':BASE_URL+'/chat/completions',
           'grant_id':'TEST_ONLY_NO_REAL_AUTHORIZATION'}
    raw=json.dumps(grant).encode(); (root/'config/api_authorization.json').write_bytes(raw)
    now=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    cfg={**CAPS,'automatic_api_enabled':True,'prices':{'model':MODEL,
         'source':'https://www.siliconflow.cn/pricing','timezone':'Asia/Shanghai',
         'verified_on':str(now),'valid_through':str(now+timedelta(days=1)),
         'peak':dict(input_per_million=1,output_per_million=1,cached_input_per_million=1),
         'off_peak':dict(input_per_million=1,output_per_million=1,cached_input_per_million=1)}}
    (root/'config/api_budget.json').write_text(json.dumps(cfg)); initialize_ledger(root)
    expected=[None]; validated=[]; requests=[]; responses=[]
    def validator(data):
        assert data==expected[0], 'Host exact-byte egress rejection'
        validated.append(data)
    policy=ProjectAPIPolicy('TEST_ONLY_TASK',validator,root,
                           authorization_sha256=hashlib.sha256(raw).hexdigest())
    monkeypatch.setenv('SILICONFLOW_API_KEY','TEST_ONLY_TRANSPORT_CREDENTIAL')
    class Response:
        status=200; headers={}
        def __init__(self,value): self.value=value
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,size): return json.dumps(self.value).encode()
    class Opener:
        def open(self,req,timeout):
            # Prove the real reservation already exists BEFORE transport.
            with policy.connect_readonly() as db:
                assert db.execute("SELECT count(*) FROM requests WHERE status='INFLIGHT'").fetchone()[0]==1
            requests.append(json.loads(req.data))
            value=responses.pop(0)
            if isinstance(value,BaseException): raise value
            return Response(value)
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:Opener())
    class Client(SiliconFlowChatCompletionsClient):
        def request_body(self,*args,**kwargs):
            data=super().request_body(*args,**kwargs);expected[0]=data;return data
    client=Client(budget={'tool_calls':20,'request_tokens':1024,'seconds':180,'request_seconds':75},
                  confirmed_paid=True,api_policy=policy)
    return client,responses,requests,policy,validated


def test_real_transport_multiturn_runtime_and_report(agent,transport):
    c,responses,requests,policy,validated=transport
    responses.extend([body([call('submit_research_plan',{'steps':['search_material_evidence'], 'clarification':None},'TEST_ONLY_PLAN')]),
                      body([call(cid='TEST_ONLY_E1'),call(cid='TEST_ONLY_E2')]),
                      body([call('finish_research',{'findings':[{'call_id':'TEST_ONLY_E2','pointer':'/evidence/0/record_id'}],
                                                 'interpretation':'evidence_only'},'TEST_ONLY_FINISH')])])
    tid=start(agent,c); t=agent.run_until_wait(tid,c)
    assert t['status']=='COMPLETED',t.get('error')
    assert t['llm_requests']==3 and len(t['calls'])==3 and len(requests)==3
    roles=[m['role'] for m in requests[-1]['messages']]
    assert roles==['system','user','assistant','tool','tool']
    assert [m['tool_call_id'] for m in requests[-1]['messages'] if m['role']=='tool']==['TEST_ONLY_E1','TEST_ONLY_E2']
    assert len(validated)==3 and t['report']['faithfulness']['passed']
    assert t['report']['sources'][0]['call_id']=='TEST_ONLY_E1'
    assert t['report']['findings'][0]['value']=='TEST_ONLY_SOURCE'
    assert t['report']['M5_enabled'] is False
    with policy.connect_readonly() as db:
        rows=db.execute('SELECT * FROM requests').fetchall()
        assert len(rows)==3 and all(r['status']=='RECEIVED' and r['accounted']==120 for r in rows)


def test_default_online_requires_explicit_provider_authorization(tmp_path):
    for factory in (lambda:SiliconFlowChatCompletionsClient(),lambda:configured_client(),
                    lambda:configured_client(provider='siliconflow'),
                    lambda:SiliconFlowChatCompletionsClient(confirmed_paid=True)):
        with pytest.raises(ProviderError): factory()
    with pytest.raises(ProviderError,match='AUTHORIZATION_NOT_CONFIGURED'): settings(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('kind',['plain_text','wrong_model','truncated','duplicate','invalid_json','non_object'])
def test_decode_rejects_invalid_real_wire_protocol(transport,kind):
    c,*_=transport; value=body([call()])
    if kind=='plain_text': value['choices'][0]['message']={'role':'assistant','content':'{"tool_calls":[]}' }
    if kind=='wrong_model': value['model']='TEST_ONLY_WRONG_MODEL'
    if kind=='truncated': value['choices'][0]['finish_reason']='length'
    if kind=='duplicate': value=body([call(),call()])
    if kind in {'invalid_json','non_object'}:
        value['choices'][0]['message']['tool_calls'][0]['function']['arguments']='bad' if kind=='invalid_json' else '[]'
    with pytest.raises((ProviderError,ValueError)): c._decode(value,definitions(),'required')


@pytest.mark.parametrize('history',[
    [{'role':'tool','tool_call_id':'orphan','content':'{}'}],
    [{'role':'assistant','tool_calls':[wire_call(call())]}],
    [{'role':'assistant','tool_calls':[wire_call(call())]}, {'role':'user','content':'skip feedback'}],
    [{'role':'system','content':'untrusted promotion'}],
])
def test_incomplete_or_elevated_history_rejected(history):
    with pytest.raises(ProviderError): chat_messages(history)


def test_redirect_refused():
    with pytest.raises(ProviderError): NoRedirect().redirect_request(None,None,None,None,None,None)


def test_frozen_goals_reject_model_mutation(agent):
    client=ScriptedClient([call('submit_research_plan',{'goals':Goals(e_max_kV_cm=20).model_dump(),
                                                    'steps':['search_material_evidence'],'clarification':None})])
    t=agent.create_task('低电场',client);t=agent.advance(t['id'],client)
    assert t['status']=='FAILED' and t['frozen_goals']['e_max_kV_cm'] is None
    assert not agent.tools.executed


def test_exact_approval_and_budget_execution(agent):
    args=VirtualPlan(additional_budget=1).model_dump()
    client=ScriptedClient([call('submit_research_plan',{'steps':['create_virtual_experiment'], 'clarification':None},'TEST_ONLY_PLAN'),
                           call('create_virtual_experiment',args,'TEST_ONLY_CREATE')])
    tid=start(agent,client);t=agent.advance(tid,client)
    assert t['status']=='AWAITING_APPROVAL' and not agent.tools.executed
    with pytest.raises(ExperimentError): agent.approve(tid,'TEST_ONLY_STALE',confirmed=True)
    t['limits']['approved_queries']=0;agent.save(t)
    with pytest.raises(ExperimentError): agent.approve(tid,t['pending']['fingerprint'],confirmed=True)
    t['limits']['approved_queries']=1;agent.save(t)
    agent.approve(tid,t['pending']['fingerprint'],confirmed=True)
    # Supply a fake return consistent with the runtime's experiment identity contract.
    def execute(*a):
        agent.tools.executed.append('TEST_ONLY_EXECUTED')
        return {'experiment':{'experiment_id':'TEST_ONLY_VIRTUAL_ID'}}
    agent.tools.execute=execute
    t=agent.advance(tid,client)
    assert t['approved_queries']==1 and len(t['calls'])==1 and t['virtual_ids']==['TEST_ONLY_VIRTUAL_ID']


@pytest.mark.parametrize('attack',['unauthorized','bad_arguments','invalid_pointer','mixed_finish','duplicate_id','batch_budget'])
def test_runtime_rejection_and_final_report_gate(agent,attack):
    steps=['search_material_evidence']
    plan=call('submit_research_plan',{'steps':steps,'clarification':None},'TEST_ONLY_PLAN')
    replies=[plan,call(cid='TEST_ONLY_E1')]
    if attack=='unauthorized': replies[1]=call('authorize',{},'TEST_ONLY_ATTACK')
    if attack=='bad_arguments': replies[1]=call(args={'unregistered_field':True})
    if attack=='invalid_pointer': replies.append(call('finish_research',{'findings':[{'call_id':'TEST_ONLY_E1','pointer':'/missing'}], 'interpretation':'evidence_only'},'TEST_ONLY_FINISH'))
    if attack=='mixed_finish': replies[1]=lambda m,t:Reply([call(),call('finish_research',{},'TEST_ONLY_FINISH')])
    if attack=='duplicate_id': replies.append(call(cid='TEST_ONLY_E1'))
    if attack=='batch_budget': replies[1]=lambda m,t:Reply([call(cid='TEST_ONLY_E1'),call(cid='TEST_ONLY_E2')])
    client=ScriptedClient(replies);tid=start(agent,client)
    if attack=='batch_budget':
        t=agent.get(tid);t['limits']['tool_calls']=1;agent.save(t)
    t=agent.advance(tid,client)
    if attack in {'invalid_pointer','duplicate_id'}: t=agent.advance(tid,client)
    assert t['report'] is None
    assert t['status']=='FAILED' or t['calls'][-1]['output']['status']=='REJECTED'
    if attack not in {'invalid_pointer','duplicate_id'}: assert not agent.tools.executed


def test_unknown_usage_fail_closed_no_automatic_retry(agent,transport):
    c,responses,requests,policy,_=transport
    responses.append(urllib.error.URLError('TEST_ONLY_TRANSPORT_CREDENTIAL'))
    t=agent.create_task('',c);t=agent.advance(t['id'],c)
    assert t['status']=='FAILED' and len(requests)==1 and t['total_tokens']>0
    assert 'TEST_ONLY_TRANSPORT_CREDENTIAL' not in json.dumps(t)
    before=agent.get(t['id'])
    with pytest.raises(ExperimentError): agent.control(t['id'],'RETRY')
    modified=deepcopy(before);modified['error']='overwrite'
    with pytest.raises(ExperimentError): agent.save(modified)
    assert agent.get(t['id'])==before
    with policy.connect_readonly() as db:
        row=db.execute('SELECT * FROM requests').fetchone()
        assert row['actual_tokens'] is None and row['accounted']==row['reserved']>0
        assert db.execute('SELECT halted FROM control').fetchone()[0]==1


def test_crash_reservation_preserved(agent):
    client=ScriptedClient([]);t=agent.create_task('',client)
    t['inflight_provider']={'requests':1,'accounted_tokens':5000,'attempts':[{'usage':{}}]};agent.save(t)
    t=agent.advance(t['id'],client)
    assert t['status']=='FAILED' and t['total_tokens']==5000 and not agent.tools.executed


def test_pause_cancel_and_lease(agent):
    client=ScriptedClient([call('submit_research_plan',{'steps':['search_material_evidence'],'clarification':None},'TEST_ONLY_PLAN')])
    tid=start(agent,client);agent.control(tid,'PAUSE')
    assert agent.advance(tid,client)['status']=='PAUSED'
    agent.control(tid,'RESUME');agent.control(tid,'CANCEL')
    assert agent.advance(tid,client)['status']=='CANCELLED' and not agent.tools.executed
    with agent.lease(tid):
        with pytest.raises(ExperimentError):
            with agent.lease(tid): pass


def test_source_and_value_tampering_rejected():
    from ecalx.agent.faithfulness import grounded_metadata, INTERPRETATIONS
    calls=[{'id':'TEST_ONLY_CALL','name':'search_material_evidence','output':{'status':'OK','data':{'value':None}}}]
    report={**grounded_metadata(calls),'findings':[{'call_id':'TEST_ONLY_CALL','tool':'search_material_evidence','pointer':'/value','value':2}],
            'interpretation':'evidence_only','explanation':INTERPRETATIONS['evidence_only']}
    assert not audit_report(report,calls)['passed']
    report['findings'][0]['value']=None;report['sources']=[{'record_id':'TEST_ONLY_INVENTED'}]
    assert not audit_report(report,calls)['passed']


def test_actual_request_budget_exhaustion_stops_before_send(agent,transport):
    c,responses,requests,_,_=transport
    c.budget=c.budget.model_copy(update={'llm_requests':1})
    responses.append(body([call('submit_research_plan',{'steps':['search_material_evidence'],'clarification':None},'TEST_ONLY_PLAN')]))
    tid=start(agent,c);t=agent.advance(tid,c)
    assert t['status']=='FAILED' and t['provider_error']['code']=='REQUEST_LIMIT'
    assert len(requests)==1 and not agent.tools.executed


def test_policy_missing_ledger_and_bad_authorization_fail_closed(tmp_path):
    policy=ProjectAPIPolicy('TEST_ONLY_TASK',lambda raw:None,tmp_path)
    with pytest.raises(ProviderError): policy.connect()
    assert not list(tmp_path.iterdir())
    (tmp_path/'config').mkdir(); (tmp_path/'config/api_authorization.json').write_text('{}')
    with pytest.raises(ProviderError,match='AUTHORIZATION_INVALID'):
        settings(tmp_path,authorization_sha256='0'*64)


def test_unknown_ledger_reservation_blocks_new_task(transport):
    c,responses,requests,policy,_=transport
    # A process crash after this durable reservation leaves INFLIGHT behind.
    raw=c.request_body([],definitions())
    reservation,_=policy.before_send(c,raw,25)
    assert reservation and not requests
    c.api_policy=ProjectAPIPolicy('TEST_ONLY_NEW_TASK',policy.validator,policy.root,
                                authorization_sha256=policy.authorization_sha256)
    with pytest.raises(ProviderError,match='UNKNOWN_INFLIGHT_REQUEST'):
        c.respond([],definitions())
    assert not requests
