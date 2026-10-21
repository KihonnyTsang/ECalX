"""TEST_HTTP_ONLY: actual transport/runtime/tools, simulated provider; NO ONLINE ACCEPTANCE."""
import json
import pytest
from ecalx_local.engine import LocalAgent,LiveClient,BUDGET,online_quote
from ecalx_local.jobs import create_research
from ecalx_local.inputs import prepare_inputs
from ecalx_local.configuration import save_config,load_credentials
from ecalx_local.byo_policy import usage_status
from test_byo import import_example


def configured():
    save_config({'model':'deepseek-ai/DeepSeek-V4-Flash'},'sk-TEST_HTTP_ONLY_NEVER_SENT')
    load_credentials()


def test_real_transport_real_policy_and_tools(isolation,tmp_path,monkeypatch):
    import ecalx.agent.siliconflow as transport
    import_example(isolation);configured()
    from ecalx_local.configuration import read_config
    # Exactly nine research requests plus two follow-up requests must fit;
    # consuming quota must not invalidate consent during the final request.
    save_config({'model':read_config()['model'],'usage_limits':{'daily_requests':11}})
    quote=online_quote();prepared=prepare_inputs({'natural_language':'探索候选组成'})
    task=create_research(prepared,'live',confirmed=True,consent=quote,storage=tmp_path/'tasks')
    agent=LocalAgent(tmp_path/'tasks');tid=task['id']
    client=LiveClient(budget=BUDGET.model_dump(),prices=None);client.bind(agent,tid)
    requests=[]
    class FakeResponse:
        status=200;headers={}
        def __init__(self,request):self.request=request
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,*_):
            raw=self.request.data;body=json.loads(raw);requests.append(body)
            assert b'raw_metadata_json' not in raw and b'protocol_fingerprint' not in raw
            assert b'/Users/' not in raw and b'sk-TEST_HTTP_ONLY_NEVER_SENT' not in raw
            assert self.request.full_url=='https://api.siliconflow.cn/v1/chat/completions'
            offered={t['function']['name'] for t in body['tools']};task=agent.get(tid)
            if 'submit_research_plan' in offered:
                name='submit_research_plan';args={'steps':['search_material_evidence','generate_material_candidates','validate_chemical_constraints','generate_research_report'],'clarification':None}
            else:
                good=[c for c in task['calls'] if c['output']['status']=='OK'];used={c['name'] for c in good}
                generated=[r for c in good if c['name']=='generate_material_candidates' for r in c['output']['data']['candidates']]
                checked={c['arguments']['formula'] for c in good if c['name']=='validate_chemical_constraints'}
                todo=next((c for c in generated if c['formula_raw'] not in checked),None)
                if 'search_material_evidence' not in used:name,args='search_material_evidence',{}
                elif 'generate_material_candidates' not in used:name,args='generate_material_candidates',{'max_candidates':4}
                elif todo:name,args='validate_chemical_constraints',{'formula':todo['formula_raw'],'family':todo['family_rule']}
                elif 'generate_research_report' not in used:name,args='generate_research_report',{}
                else:
                    name='finish_research';tool=next(t['function'] for t in body['tools'] if t['function']['name']==name)
                    ids=tool['parameters']['$defs']['Finding']['properties']['call_id']['enum']
                    args={'findings':[{'call_id':cid,'pointer':''} for cid in ids],'interpretation':'unverified_candidates'}
            assert name in offered
            return json.dumps({'id':'TEST_HTTP_ONLY','model':client.model,
                'usage':{'prompt_tokens':1000,'completion_tokens':100,'total_tokens':1100},
                'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                    'tool_calls':[{'id':'test-http-'+str(len(requests)),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}]}).encode()
    class FakeOpener:
        def open(self,request,**_):return FakeResponse(request)
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *_:FakeOpener())
    from ecalx_local.worker import run
    run(tid,agent.storage_root)
    agent.confirm_plan(tid,confirmed=True)
    run(tid,agent.storage_root)
    task=agent.get(tid)
    assert task['status']=='COMPLETED',task.get('error')
    assert len(requests)==9 and task['report']['faithfulness']['passed']
    assert usage_status()['cost'] is None and usage_status()['unknown']==0
    assert usage_status()['requests']==9 and usage_status()['tokens']==9900
    # Same real follow-up transport/policy, simulated responses.
    from ecalx_local.conversation import Conversation
    from ecalx_local.conversation_live import FollowupClient,followup_quote
    from ecalx_local import conversation_live
    monkeypatch.setattr(conversation_live,'SCIENCE_ROOT',isolation)
    conversation=Conversation(agent.storage_root);q=followup_quote()
    turn=conversation.create(tid,'总结研究证据',mode='live',consent=q)
    follow=FollowupClient(prices=None);bodies=[]
    class FollowResponse(FakeResponse):
        def read(self,*_):
            body=json.loads(self.request.data);bodies.append(body)
            name=body['tools'][0]['function']['name']
            return json.dumps({'id':'TEST_HTTP_ONLY_FOLLOWUP','model':follow.model,'usage':{'prompt_tokens':1000,'completion_tokens':100,'total_tokens':1100},
                'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[
                    {'id':'follow-'+str(len(bodies)),'type':'function','function':{'name':name,'arguments':json.dumps({'block_ids':['overview','gaps']})}}]}}]}).encode()
    class FollowOpener:
        def open(self,request,**_):return FollowResponse(request)
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *_:FollowOpener())
    done=conversation.run(tid,turn['id'],follow)
    assert done['status']=='COMPLETED' and len(bodies)==2,done
    assert usage_status()['requests']==11 and usage_status()['tokens']==12100
    assert agent.get(tid)==task
    with pytest.raises(Exception):followup_quote()


def test_request_gates_fail_before_http(isolation,tmp_path,monkeypatch):
    import ecalx.agent.siliconflow as transport
    configured();prepared=prepare_inputs({});quote=online_quote()
    with pytest.raises(ValueError):create_research(prepared,'live',confirmed=False,consent=quote,storage=tmp_path/'tasks')
    with pytest.raises(ValueError):create_research(prepared,'live',confirmed=True,consent=None,storage=tmp_path/'tasks')
    task=create_research(prepared,'live',confirmed=True,consent=quote,storage=tmp_path/'tasks')
    agent=LocalAgent(tmp_path/'tasks');client=LiveClient(budget=BUDGET.model_dump(),prices=None);client.bind(agent,task['id'])
    def forbidden(*args,**kwargs):pytest.fail('HTTP must not execute without current consent')
    monkeypatch.setattr(transport.urllib.request,'build_opener',forbidden)
    changed=agent.get(task['id']);changed['online_consent']=None;agent.save(changed)
    failed=agent.advance(task['id'],client)
    assert failed['status']=='FAILED'
    with pytest.raises(Exception):
        failed['status']='DRAFT';agent.save(failed)
    assert agent.get(task['id'])['status']=='FAILED'


def test_unknown_usage_keeps_reservation_and_blocks_send(isolation,tmp_path,monkeypatch):
    import ecalx.agent.siliconflow as transport
    configured();quote=online_quote()
    task=create_research(prepare_inputs({}),'live',confirmed=True,consent=quote,storage=tmp_path/'tasks')
    agent=LocalAgent(tmp_path/'tasks');client=LiveClient(budget=BUDGET.model_dump(),prices=None);client.bind(agent,task['id'])
    class Broken:
        def open(self,*args,**kwargs):raise OSError('TEST_HTTP_ONLY connection lost')
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *_:Broken())
    assert agent.advance(task['id'],client)['status']=='FAILED'
    assert usage_status()['unknown']==1 and usage_status()['tokens']>0
    assert usage_status()['cost'] is None
    with pytest.raises(Exception):online_quote()
