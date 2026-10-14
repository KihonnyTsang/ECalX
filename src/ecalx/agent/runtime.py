"""Explicit persisted state machine with exact-call approval and crash-safe receipts.

Trusted UI methods are deliberately absent from the LLM tool registry.
"""
import fcntl
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from pydantic import ValidationError

from ecalx.domain.constraints import DiscoveryRequest
from ecalx.experiments import RealLaboratory, VirtualLaboratory
from ecalx.experiments.contracts import ExperimentError, ReviewInput, AuthorizeUpdateInput
from ecalx.experiments.storage import Store, dump, digest, identifier
from .contracts import INPUTS, STATEFUL, Plan, Finish, ToolOutput, Goals
from .llm import ProviderError, redact
from .tools import ScientificTools, ReadOnlyEvidence
from .capabilities import available_tools
from .faithfulness import grounded_metadata, audit_report, INTERPRETATIONS

STATUS = {'DRAFT','NEEDS_CLARIFICATION','AWAITING_APPROVAL','RUNNING','PAUSED','COMPLETED','FAILED','CANCELLED'}
LIMITS = {'tool_calls':32, 'steps':48, 'seconds':300, 'approved_queries':100, 'retries':3,
          'provider_output_tokens':16384, 'context_bytes':180000}


def resolve_pointer(data, pointer):
    if pointer and not pointer.startswith('/'):
        raise ExperimentError('引用必须是JSON Pointer')
    for part in pointer.split('/')[1:] if pointer else []:
        key=part.replace('~1','/').replace('~0','~')
        data=data[int(key)] if isinstance(data,list) else data[key]
    return data


class ResearchAgent(Store):
    def __init__(self, root, storage_root=None, evidence_path=None, *, tools_factory=None):
        self.root=Path(root)
        self.storage_root=Path(storage_root or self.root/'data/experiments/research_agent')
        super().__init__(self.storage_root/'tasks.sqlite3')
        repository=ReadOnlyEvidence(evidence_path or self.root/'data/processed/evidence.sqlite3')
        self.tools=(tools_factory(self.root,repository) if tools_factory else
            ScientificTools(self.root,repository,VirtualLaboratory(self.storage_root,self.root),RealLaboratory(self.storage_root)))
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS research_tasks(id TEXT PRIMARY KEY, payload TEXT)')

    def save(self, task):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM research_tasks WHERE id=?',(task['id'],)).fetchone()
            current=json.loads(row[0]) if row else {}
            if current.get('status') == 'FAILED' and current.get('client_mode') == 'LIVE_LLM':
                if current != redact(task):
                    raise ExperimentError('真实API失败记录不可改写；需独立新任务和项目策略检查')
                return
            if (current.get('frozen_goals') is not None and
                    current.get('goal_revision') == task.get('goal_revision') and
                    current['frozen_goals'] != task.get('frozen_goals')):
                raise ExperimentError('冻结Goals不可由任务保存覆盖')
            if task.get('plan') and task.get('frozen_goals') is not None and task['plan']['goals'] != task['frozen_goals']:
                raise ExperimentError('计划Goals与宿主冻结值不一致')
            # A worker saving a reservation/result must not overwrite trusted controls.
            if current.get('status') in {'PAUSED','CANCELLED'}:
                task['status']=current['status']
                task['approvals']=current['approvals']
                if 'resume_status' in current:
                    task['resume_status']=current['resume_status']
            db.execute('UPDATE research_tasks SET payload=? WHERE id=?',(dump(redact(task)),task['id']))

    def get(self, tid):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM research_tasks WHERE id=?',(tid,)).fetchone()
        if not row:
            raise ExperimentError('未知任务')
        return json.loads(row[0])

    def list_tasks(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM research_tasks ORDER BY rowid DESC')]

    def log(self, tid, action, detail):
        with self.connect() as db:
            self.event(db,action,tid,redact(detail))

    @contextmanager
    def lease(self, tid):
        # One executor per task across processes; controls may still pause/cancel between steps.
        with (self.storage_root/(tid+'.lock')).open('a') as handle:
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise ExperimentError('任务已有执行器；重复执行已拦截') from None
            try:
                yield
            finally:
                fcntl.flock(handle,fcntl.LOCK_UN)

    def create_task(self, original, client, budget=None):
        if len(original)>8000:
            raise ExperimentError('用户需求超过8000字符限制')
        from .siliconflow import LLMBudget
        resources=LLMBudget.model_validate(budget or getattr(client, 'budget', LLMBudget(llm_requests=48)))
        from .planning import input_goals
        frozen_goals, preferences = input_goals(original)
        tid=identifier('task')
        task={'id':tid,'original':redact(original),'status':'DRAFT','plan':None,'plan_confirmed':False,
              'frozen_goals':frozen_goals,'goal_revision':0,'blocking_questions':[],
              'optional_preferences':preferences,'goals_authority':'USER_INPUT_PENDING_CONFIRMATION',
              'goal_origins':{k:'USER_INPUT' for k,v in frozen_goals.items() if v not in (None,False,[], '')},
              'client_mode':client.mode,'provider_model':client.model,'calls':[],'pending':None,'uploads':[],
              'virtual_ids':[],'real_ids':[],'approvals':{},'cache':{},'steps':0,'seconds':0.,'retries':0,
              'output_tokens':0,'approved_queries':0,'clarification_history':[],'messages':[], 'report':None,
              'limits':{**LIMITS, **resources.model_dump()},'M5_enabled':False,
              'provider':getattr(client,'provider',client.__class__.__name__),
              'llm_requests':0,'total_tokens':0,'call_queue':[],'provider_call_ids':[],
              'provider_responses':[],'token_statistics':{'input_tokens':0,'output_tokens':0,
                  'reported_total_tokens':0,'unknown_usage_requests':0,'estimated_cost_cny':None,
                  'unknown_cost_requests':0},
              'token_prices':getattr(client,'prices',None).model_dump() if getattr(client,'prices',None) else None}
        with self.connect() as db:
            db.execute('INSERT INTO research_tasks VALUES(?,?)',(tid,dump(task)))
            self.event(db,'CREATE_TASK',tid,{'client_mode':client.mode,'model':client.model,
                'frozen_goals':frozen_goals,'optional_preferences':preferences,
                'goals_authority':'USER_INPUT_PENDING_CONFIRMATION'})
        return task

    def attach_upload(self,tid,filename,content,choice,*,submitted=False):
        if submitted is not True or choice not in {'SKIP_FEEDBACK','SAVE_ONLY','REQUEST_MODEL_UPDATE'}:
            raise ExperimentError('文件只能由用户主动提交并选择反馈权限')
        task=self.get(tid)
        if task['plan'] or task['status']!='DRAFT':
            raise ExperimentError('文件必须在计划前提交；请新建任务')
        if Path(filename).suffix.lower() not in {'.csv','.xlsx'} or not content or len(content)>20*1024*1024:
            raise ExperimentError('仅接受20MB以内CSV/XLSX')
        sha=hashlib.sha256(content).hexdigest()
        previous=next((u for u in task['uploads'] if u['sha256']==sha and u['choice']==choice),None)
        if previous:
            return previous
        uid=identifier('upload'); folder=self.storage_root/'uploads';folder.mkdir(exist_ok=True)
        path=folder/(uid+Path(filename).suffix.lower());path.write_bytes(content)
        upload={'upload_id':uid,'filename':Path(filename).name,'sha256':sha,'choice':choice,'path':str(path)}
        task['uploads'].append(upload);self.save(task);self.log(tid,'HUMAN_UPLOAD',{k:v for k,v in upload.items() if k!='path'})
        return upload

    def establish_goals(self, tid, goals, *, confirmed=False, blocking_questions=()):
        """Trusted user entry before planning, never a model capability."""
        task = self.get(tid)
        if confirmed is not True or task['status'] != 'DRAFT' or task['plan'] or task['llm_requests']:
            raise ExperimentError('Goals需用户明确确认，且必须在规划请求之前建立')
        value = Goals.model_validate(goals, strict=True).model_dump()
        if any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in blocking_questions):
            raise ExperimentError('阻塞问题必须是明确的宿主审核问题')
        task.update(frozen_goals=value, goal_revision=task.get('goal_revision',0)+1,
                    blocking_questions=list(blocking_questions), goals_authority='HUMAN_CONFIRMED_USER_INPUT',
                    goal_origins={k:'HUMAN_CONFIRMED_USER_INPUT' for k,v in value.items() if v not in (None,False,[], '')})
        self.save(task)
        self.log(tid, 'HUMAN_ESTABLISH_FROZEN_GOALS', {'goals':value, 'blocking_questions':list(blocking_questions)})
        return task

    def confirm_plan(self,tid,*,confirmed=False,accept_unquantified=False):
        task=self.get(tid)
        if confirmed is not True or not task['plan'] or task['status'] not in {'DRAFT','NEEDS_CLARIFICATION'}:
            raise ExperimentError('需要用户确认当前科研约束和计划')
        if task['plan']['goals'] != task.get('frozen_goals'):
            raise ExperimentError('计划Goals与冻结值不一致')
        if task['plan']['clarification']:
            raise ExperimentError('阻塞问题必须通过补充需求解决，不能代签确认')
        task['goals_authority']='HUMAN_CONFIRMED_USER_INPUT'
        task['plan_confirmed']=True;task['status']='RUNNING'
        task['clarification_history'].append({'action':'confirm','unquantified_preference':accept_unquantified,'plan':task['plan']})
        self.save(task);self.log(tid,'HUMAN_CONFIRM_PLAN',task['clarification_history'][-1])
        return task

    def clarify(self,tid,text):
        task=self.get(tid)
        if task['status'] not in {'DRAFT','NEEDS_CLARIFICATION'} or not text.strip():
            raise ExperimentError('当前状态不接受需求修改；请新建任务')
        task['clarification_history'].append({'action':'clarify','text':redact(text)})
        task['original']+='\n补充科研需求：'+redact(text)
        from .planning import input_goals
        task['frozen_goals'], task['optional_preferences'] = input_goals(task['original'])
        task['goal_revision']=task.get('goal_revision',0)+1
        task['blocking_questions']=[]
        task['goals_authority']='USER_INPUT_PENDING_CONFIRMATION'
        task['goal_origins']={k:'USER_INPUT' for k,v in task['frozen_goals'].items() if v not in (None,False,[], '')}
        task.update(status='DRAFT',plan=None,pending=None,messages=[])
        self.save(task);self.log(tid,'HUMAN_CLARIFY',{'text':text})

    def approve(self,tid,fingerprint,*,confirmed=False):
        task=self.get(tid); pending=task['pending']
        if confirmed is not True or task['status']!='AWAITING_APPROVAL' or not pending or pending['fingerprint']!=fingerprint:
            raise ExperimentError('必须确认当前精确操作；旧审批不能授权改变后的参数')
        cost=pending['preview'].get('budget',{}).get('total_queries',0)
        if task['approved_queries']+cost>task['limits']['approved_queries']:
            raise ExperimentError('任务总资源限额不足；不能通过新工具调用增加限额')
        if fingerprint not in task['approvals']:
            task['approved_queries']+=cost
        task['approvals'][fingerprint]={'preview':pending['preview'],'explicit_human':True}
        task['status']='RUNNING';self.save(task);self.log(tid,'HUMAN_APPROVE',task['approvals'][fingerprint])
        return task

    def control(self,tid,action):
        with self.connect() as db:
            task=json.loads(db.execute('SELECT payload FROM research_tasks WHERE id=?',(tid,)).fetchone()[0])
            if task['status'] == 'FAILED' and task['client_mode'] == 'LIVE_LLM':
                raise ExperimentError('真实API失败任务不可恢复或覆盖；需独立新任务')
            if action=='CANCEL' and task['status'] not in {'COMPLETED','CANCELLED'}:
                task['status']='CANCELLED';task['approvals']={}
            elif action=='PAUSE' and task['status'] in {'RUNNING','AWAITING_APPROVAL'}:
                task['resume_status']=task['status'];task['status']='PAUSED'
            elif action=='RESUME' and task['status']=='PAUSED':
                task['status']=task.pop('resume_status','RUNNING')
            elif action=='RETRY' and task['status']=='FAILED' and task['retries']<task['limits']['retries']:
                task['retries']+=1;task['status']='RUNNING' if task['plan_confirmed'] else 'DRAFT'
                task.pop('error',None)
            else:
                raise ExperimentError('当前状态不允许该操作')
            db.execute('UPDATE research_tasks SET payload=? WHERE id=?',(dump(task),tid))
            self.event(db,'HUMAN_'+action,tid,{'status':task['status']})
        return task

    def update_task(self,source_tid,eid,client,*,requested=False):
        source=self.get(source_tid)
        if requested is not True or eid not in source['real_ids']:
            raise ExperimentError('需用户主动提出已保存数据的REQUEST_MODEL_UPDATE')
        task=self.create_task('REQUEST_MODEL_UPDATE：请为已保存真实文件创建独立申请，保留原SAVE_ONLY。',client)
        task['real_ids']=[eid];task['update_subject']=eid
        self.save(task);self.log(task['id'],'HUMAN_REQUEST_UPDATE',{'source_task':source_tid,'experiment_id':eid})
        return task

    def review_update(self,tid,rid,note,*,confirmed=False,admit=False):
        task=self.get(tid)
        matching=[r for r in self.tools.real.list_experiments() if r['experiment_id'] in task['real_ids']
                  and r['update_request'] and r['update_request']['id']==rid]
        if confirmed is not True or not note.strip() or not matching:
            raise ExperimentError('需要独立的人工审核/授权和记录')
        lab=self.tools.real
        if admit:
            token=lab.authorize('AUTHORIZE_REAL_UPDATE',rid,'human',confirmed=True)
            result=lab.authorize_model_update(AuthorizeUpdateInput(request_id=rid,authorization_id=token))
        else:
            token=lab.authorize('REVIEW_REAL',rid,'human',confirmed=True)
            result=lab.review(ReviewInput(request_id=rid,decision='APPROVE',reviewer='human',note=note,authorization_id=token))
        self.log(tid,'HUMAN_ADMIT' if admit else 'HUMAN_REVIEW',{'note':note,'result':result.model_dump()})
        return result

    def public(self,task):
        data={k:v for k,v in task.items() if k not in {'messages','cache','call_queue'}}
        data['uploads']=[{k:v for k,v in u.items() if k!='path'} for u in task['uploads']]
        data['audit']=[r for r in self.audit() if r['subject']==task['id']]
        data['calls']=[{k:v for k,v in c.items() if k!='provider_items'} for c in task['calls']]
        if data.get('pending'):
            data['pending']={k:v for k,v in data['pending'].items() if k!='provider_items'}
        return redact(data)

    def context(self,task):
        header={k:task[k] for k in ('original','plan','client_mode','virtual_ids','real_ids')}
        header.update(frozen_goals=task.get('frozen_goals'), optional_preferences=task.get('optional_preferences', []),
                      blocking_questions=task.get('blocking_questions', []))
        header['uploads']=[{k:v for k,v in u.items() if k!='path'} for u in task['uploads']]
        header['available_capabilities']=[t['name'] for t in available_tools(task)]
        header['scientific_boundaries']=grounded_metadata([])['scientific_boundaries']
        header['request_index']=task.get('llm_requests',0)
        if task.get('update_subject'):
            header['update_subject']=task['update_subject']
        messages=[{'role':'user','content':dump(header)}]+task['messages']
        if len(dump(messages).encode())>task['limits']['context_bytes']:
            raise ProviderError('上下文资源限额耗尽；完整结果仍保存在本地任务轨迹')
        return messages

    def finish(self,task,args):
        successful={s['name']:s['output']['data'] for s in task['calls'] if s.get('output',{}).get('status')=='OK'}
        required={'unverified_candidates':'generate_material_candidates','virtual_results_only':None,
                  'saved_not_training':'import_real_experiment','review_required':None,'unsupported_domain':'check_model_applicability'}
        prerequisite=required.get(args.interpretation)
        if prerequisite and prerequisite not in successful:
            raise ExperimentError('解释类型缺少对应实际工具结果')
        if args.interpretation=='virtual_results_only' and not set(successful)&{'create_virtual_experiment','compare_optimization_strategies','query_virtual_experiment'}:
            raise ExperimentError('没有实际虚拟结果')
        if args.interpretation=='review_required' and not set(successful)&{'request_model_update','import_real_experiment'}:
            raise ExperimentError('没有实际反馈申请')
        if args.interpretation=='unsupported_domain' and successful['check_model_applicability'].get('supported') is not False:
            raise ExperimentError('工具并未拒绝此定义域')
        if args.interpretation=='saved_not_training' and successful['import_real_experiment'].get('feedback_choice')!='SAVE_ONLY':
            raise ExperimentError('非SAVE_ONLY结果不能解释为仅保存')
        findings=[]
        for ref in args.findings:
            row=next((s for s in task['calls'] if s['id']==ref.call_id and s.get('output',{}).get('status')=='OK'),None)
            if row is None:
                raise ExperimentError('报告引用必须指向成功的实际工具结果')
            try:
                value=resolve_pointer(row['output']['data'],ref.pointer)
            except (KeyError,ValueError,IndexError,TypeError):
                raise ExperimentError('报告引用路径不存在') from None
            findings.append({'call_id':ref.call_id,'pointer':ref.pointer,'tool':row['name'],'value':value})
        if not findings:
            raise ExperimentError('请引用实际成功工具结果，不能用无依据的结论完成任务')
        task['report']={'task_id':task['id'],'original':task['original'],'goals':task['plan']['goals'],
            'interpretation':args.interpretation,
            'explanation':INTERPRETATIONS[args.interpretation],'findings':findings,
            'notice':'所有数值及来源均引用确定性工具。候选性能未验证；M5禁用。','M5_enabled':False}
        task['report'].update(grounded_metadata(task['calls']))
        task['report']['faithfulness']=audit_report(task['report'],task['calls'])
        if not task['report']['faithfulness']['passed']:
            task['report']=None
            raise ExperimentError('科学报告忠实性核验失败')
        task['status']='COMPLETED'

    def record_provider(self,task,metrics,**details):
        task['llm_requests']=task.get('llm_requests',0)+metrics.get('requests',0)
        task['total_tokens']=task.get('total_tokens',0)+metrics.get('accounted_tokens',0)
        stats=task.setdefault('token_statistics',{'input_tokens':0,'output_tokens':0,
            'reported_total_tokens':0,'unknown_usage_requests':0,'estimated_cost_cny':None,'unknown_cost_requests':0})
        for attempt in metrics.get('attempts',[]):
            usage=attempt.get('usage',{})
            for field in ('input_tokens','output_tokens'):
                stats[field]+=int(usage.get(field,0) or 0)
            stats['reported_total_tokens']+=int(usage.get('total_tokens',0) or 0)
            if usage.get('total_tokens') is None:
                stats['unknown_usage_requests']+=1
            cost=usage.get('estimated_cost_cny')
            if cost is None:
                stats['unknown_cost_requests']+=1
            else:
                stats['estimated_cost_cny']=(stats['estimated_cost_cny'] or 0)+cost
        task.setdefault('provider_responses',[]).append(redact({**details,'metrics':metrics,
            'text_status':'UNVERIFIED_MODEL_TEXT_NOT_SCIENTIFIC_EVIDENCE'}))

    def available_tools(self, task):
        return available_tools(task)

    def validate_batch(self, task, batch, offered):
        """Optional host-owned admission gate, before queuing any batch effects."""

    def advance(self,tid,client):
        with self.lease(tid):
            task=self.get(tid)
            if task['status'] not in {'DRAFT','RUNNING'} or (task['status']=='DRAFT' and task['plan']):
                return task
            if ((task['client_mode'],task['provider_model'])!=(client.mode,client.model)
                    or task.get('provider',getattr(client,'provider',client.__class__.__name__)) != getattr(client,'provider',client.__class__.__name__)):
                raise ExperimentError('恢复必须使用同一提供方/模型；变更需新建任务')
            if hasattr(client,'budget'):
                from .siliconflow import LLMBudget, TokenPrices
                client.budget=LLMBudget.model_validate({k:v for k,v in task['limits'].items() if k in LLMBudget.model_fields})
                client.prices=TokenPrices.model_validate(task['token_prices']) if task.get('token_prices') else None
            start=time.monotonic()
            try:
                if task.get('inflight_provider'):
                    self.record_provider(task,task.pop('inflight_provider'),error={'code':'INTERRUPTED_UNKNOWN_USAGE'})
                    raise ProviderError('前次请求中断，用量已保守记账；请人工核对后重试',code='INTERRUPTED_UNKNOWN_USAGE')
                if task['steps']>=task['limits']['steps'] or task['seconds']>=task['limits']['seconds'] or task['output_tokens']>=task['limits']['provider_output_tokens']:
                    raise ProviderError('任务执行资源上限已达到')
                task['steps']+=1
                if not task['pending'] and not task.get('call_queue'):
                    if task.get('llm_requests',0)>=task['limits'].get('llm_requests',48):
                        raise ProviderError('LLM请求次数上限已达到',code='REQUEST_LIMIT')
                    messages=self.context(task)
                    offered=self.available_tools(task)
                    self.log(tid,'CAPABILITIES_EXPOSED',{'tools':[t['name'] for t in offered],
                        'state':task['status'],'stateful_permission':'PROPOSAL_ONLY_EXACT_APPROVAL_REQUIRED'})
                    def provider_checkpoint():
                        if self.get(tid)['status'] in {'PAUSED','CANCELLED'}:
                            raise ProviderError('任务已暂停/取消；停止后续请求和工具执行',code='CANCELLED')
                    def reserve_attempt(metrics):
                        task['inflight_provider']=redact(metrics)
                        self.save(task)
                    try:
                        if hasattr(client,'respond_budgeted'):
                            remaining=task['limits'].get('total_tokens',131072)-task.get('total_tokens',0)
                            remaining_seconds=task['limits']['seconds']-task['seconds']-(time.monotonic()-start)
                            reply=client.respond_budgeted(messages,offered,
                                remaining_requests=task['limits'].get('llm_requests',48)-task.get('llm_requests',0),
                                remaining_tokens=remaining,timeout=min(task['limits'].get('request_seconds',25),remaining_seconds),
                                checkpoint=provider_checkpoint,on_attempt=reserve_attempt)
                        else:
                            reply=client.respond(messages,offered)
                    except ProviderError as error:
                        task.pop('inflight_provider',None)
                        self.record_provider(task,error.metrics or {'requests':0 if hasattr(client,'respond_budgeted') else 1,'accounted_tokens':0},error=error.public())
                        raise
                    task.pop('inflight_provider',None)
                    self.record_provider(task,reply.metrics or {'requests':1,'accounted_tokens':int(reply.usage.get('total_tokens',0)),
                        'attempts':[{'usage':reply.usage}]},model=reply.model,visible_text=reply.visible_text)
                    task['output_tokens']+=int(reply.usage.get('output_tokens',0) or 0)
                    provider_checkpoint()
                    if task['seconds']+time.monotonic()-start>=task['limits']['seconds']:
                        raise ProviderError('任务累计执行时限耗尽；拒绝迟到计划或完成调用',code='TIMEOUT')
                    if task.get('total_tokens',0)>task['limits'].get('total_tokens',131072):
                        raise ProviderError('任务累计Token上限已达到',code='TOKEN_LIMIT')
                    if task['output_tokens']>task['limits']['provider_output_tokens']:
                        raise ProviderError('提供方输出token资源限额耗尽')
                    self.log(tid,'PROVIDER_RESPONSE',{'mode':client.mode,'model':reply.model,'usage':reply.usage,'metrics':reply.metrics})
                    if reply.output_items and reply.output_items[0].get('role')=='assistant':
                        self.log(tid,'PROVIDER_TOOL_CALLS',{'source':'message.tool_calls',
                            'tool_calls':reply.output_items[0].get('tool_calls',[])})
                    tool_history=[m['tool_call_id'] for m in messages if m.get('role')=='tool']
                    if tool_history:
                        self.log(tid,'TOOL_FEEDBACK_ACCEPTED',{'role':'tool','call_ids':tool_history,'response_model':reply.model})
                    if not reply.calls or (not task['plan'] and len(reply.calls)!=1):
                        raise ProviderError('规划需要单个结构化调用，执行需要实际工具调用')
                    batch=redact(reply.calls)
                    ids=[]
                    for call in batch:
                        if (len(dump(call).encode())>16000 or not isinstance(call.get('id'),str)
                                or not 0<len(call['id'])<=100 or not isinstance(call.get('arguments'),dict)):
                            raise ProviderError('工具调用大小/ID/参数非法')
                        ids.append(call['id'])
                    old_ids=set(task.get('provider_call_ids',[])) | {c['id'] for c in task['calls']}
                    if len(set(ids))!=len(ids) or old_ids.intersection(ids):
                        raise ProviderError('提供方重复call_id；拒绝不完整协议')
                    if len(batch)>1 and any(c['name']=='finish_research' for c in batch):
                        raise ProviderError('完成报告必须单独调用，不能跳过同批工具结果')
                    task.setdefault('provider_call_ids',[]).extend(ids)
                    call=batch[0]
                    if not task['plan']:
                        if call['name']!='submit_research_plan':
                            raise ProviderError('首先需要结构化科研计划')
                        from .planning import accept_proposal
                        if 'frozen_goals' not in task:
                            raise ProviderError('旧任务没有冻结Goals；需新建任务', code='PLAN_REJECTED')
                        task['plan']=accept_proposal(call['arguments'], task['frozen_goals'],
                                                     task.get('blocking_questions', []))
                        task['status']='NEEDS_CLARIFICATION' if task['plan']['clarification'] else 'DRAFT'
                        self.log(tid,'PLAN',task['plan']);self.save(task)
                        return task
                    if len(task['calls'])+len(batch)>task['limits']['tool_calls']:
                        raise ProviderError('工具调用次数上限不足以执行整批调用',code='TOOL_LIMIT')
                    self.validate_batch(task, batch, offered)
                    chat_batch=bool(reply.output_items and reply.output_items[0].get('role')=='assistant')
                    task['call_queue']=[{**item,'provider_items':redact(reply.output_items) if index==0 else [],
                        'offered_tools':[t['name'] for t in offered],
                        'chat_batch':chat_batch,'fingerprint':digest([tid,item['name'],item['arguments']]),'preview':{}}
                        for index,item in enumerate(batch)]
                    self.save(task)  # Persist the complete model decision before any effect.
                if not task['pending']:
                    task['pending']=task['call_queue'].pop(0)
                    self.save(task)
                    self.log(tid,'TOOL_PROPOSED',{k:v for k,v in task['pending'].items() if k!='provider_items'})
                pending=task['pending'];name=pending['name']
                if len(task['calls'])>=task['limits']['tool_calls']:
                    raise ProviderError('工具调用次数上限已达到',code='TOOL_LIMIT')
                try:
                    allowed=pending.get('offered_tools',[t['name'] for t in self.available_tools(task)])
                    if name not in allowed:
                        raise ExperimentError('工具不在本轮合法能力集合中；未授权调用已拦截')
                    if name=='finish_research':
                        args=Finish.model_validate(pending['arguments'],strict=True)
                        pending['validated_arguments']=args.model_dump()
                        self.finish(task,args)
                        output=ToolOutput(data={'report':task['report']}).model_dump()
                    else:
                        if name not in INPUTS or name not in task['plan']['steps']:
                            raise ExperimentError('工具未注册或不在已确认计划中')
                        args=INPUTS[name].model_validate(pending['arguments'],strict=True)
                        pending['validated_arguments']=args.model_dump()
                        fingerprint=pending['fingerprint']
                        if fingerprint in task['cache']:
                            output=task['cache'][fingerprint]
                            self.log(tid,'IDEMPOTENT_REPLAY',{'fingerprint':fingerprint})
                        else:
                            preview=self.tools.preview(name,args,task,fingerprint)
                            pending['preview']=redact(preview)
                            if name in STATEFUL and fingerprint not in task['approvals']:
                                task['status']='AWAITING_APPROVAL';self.save(task)
                                self.log(tid,'APPROVAL_REQUIRED',{'fingerprint':fingerprint,'preview':preview})
                                return task
                            def checkpoint():
                                status=self.get(tid)['status']
                                if status in {'PAUSED','CANCELLED'}:
                                    raise ProviderError('任务已暂停/取消；在原子实验轮次边界停止')
                                if task['seconds']+time.monotonic()-start>=task['limits']['seconds']:
                                    raise ProviderError('任务累计执行时限耗尽；在原子轮次边界停止',code='TIMEOUT')
                            task_with_request={**task,'goals_request':DiscoveryRequest(**task['plan']['goals'],natural_language=task['original'],
                                confirmed_ambiguities=tuple([task['plan']['clarification']] if task['plan']['clarification'] else [])),'checkpoint':checkpoint}
                            checkpoint()
                            self.log(tid,'PYTHON_TOOL_STARTED',{'call_id':pending['id'],'tool':name,'validated_arguments':args.model_dump()})
                            result=self.tools.execute(name,args,task_with_request,fingerprint)
                            output=ToolOutput(data=redact(result)).model_dump()
                            if name in STATEFUL:
                                task['cache'][fingerprint]=output
                            eids=[]
                            if name=='create_virtual_experiment':
                                eids=[result['experiment']['experiment_id']]
                            elif name=='compare_optimization_strategies':
                                eids=[r['experiment']['experiment_id'] for r in result['runs']]
                            task['virtual_ids']=list(dict.fromkeys(task['virtual_ids']+eids))
                            if name in {'import_real_experiment','request_model_update'} and result.get('experiment_id'):
                                task['real_ids']=list(dict.fromkeys(task['real_ids']+[result['experiment_id']]))
                except (ExperimentError,ValidationError,ValueError,KeyError) as error:
                    output=ToolOutput(status='REJECTED',error=redact(str(error)[:2000])).model_dump()
                task['calls'].append({**pending,'output':output,'elapsed_seconds':time.monotonic()-start})
                # Bound model context, retain full structured output locally. Truncation is explicit.
                encoded=dump(output)
                if len(encoded)>18000:
                    encoded=dump({'status':output['status'],'data':{'local_result_available':True,
                        'call_id':pending['id'],'keys':list(output['data']),
                        'summary':output['data'].get('summary'),'schema_version':output['data'].get('schema_version'),
                        'data_version':output['data'].get('data_version'),'message':output['data'].get('message'),
                        'evidence_only':output['data'].get('evidence_only',[])[:2],
                        'candidates':output['data'].get('candidates',[])[:2]},'notice':'完整结果存于本地轨迹，可用根pointer引用'})
                if pending.get('chat_batch'):
                    task['messages'] += pending.get('provider_items',[])+[{'role':'tool','tool_call_id':pending['id'],'content':encoded}]
                else:
                    provider_items=pending.get('provider_items') or [{'type':'function_call','call_id':pending['id'],'name':name,'arguments':dump(pending['arguments'])}]
                    task['messages'] += provider_items+[{'type':'function_call_output','call_id':pending['id'],'output':encoded}]
                self.log(tid,'TOOL_RESULT_FEEDBACK',{'call_id':pending['id'],'role':'tool' if pending.get('chat_batch') else 'function_call_output',
                    'content':encoded,'delivery_status':'QUEUED_FOR_NEXT_REQUEST'})
                task.pop('error',None)
                task['pending']=None
                self.log(tid,'TOOL_RESULT',{'call_id':pending['id'],'tool':name,'output':output})
            except Exception as error:
                task['status']='FAILED';task['error']=redact(type(error).__name__+':'+str(error)[:1000]) if isinstance(error,(ProviderError,ExperimentError,ValidationError)) else type(error).__name__
                if isinstance(error,ProviderError):
                    task['provider_error']=error.public()
                self.log(tid,'FAILURE',{'error':task['error'],'pending':{k:v for k,v in (task['pending'] or {}).items() if k!='provider_items'}})
            finally:
                task['seconds']+=time.monotonic()-start
                # Honor a trusted pause/cancel that arrived while an atomic tool was executing.
                current=self.get(tid)
                if current['status'] in {'PAUSED','CANCELLED'}:
                    task['status']=current['status'];task['approvals']=current['approvals']
                    if 'resume_status' in current:
                        task['resume_status']=current['resume_status']
                self.save(task)
            return task

    def run_until_wait(self,tid,client):
        for _ in range(LIMITS['steps']):
            task=self.advance(tid,client)
            if task['status']!='RUNNING':
                return task
        return self.get(tid)

    def markdown(self,tid):
        task=self.get(tid)
        report=task['report'] or {'notice':'任务尚未完成；详见工具执行轨迹'}
        return '# ECalX Research Agent 报告\n\n模式：'+task['client_mode']+'\n\n```json\n'+json.dumps(redact(report),ensure_ascii=False,indent=2)+'\n```\n'
