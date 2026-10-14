"""Deterministic tool implementations; capabilities injected by the trusted runtime."""
import json
import sqlite3
from pathlib import Path

from ecalx.data.repository import EvidenceRepository
from ecalx.services.automatic import explore_materials, reference
from ecalx.services.discovery import discover
from ecalx.materials.chemistry import parse_formula, exact_key
from ecalx.materials.generator import REGISTRY
from ecalx.experiments.adapters import SIMULATOR
from ecalx.experiments.candidate_pool import public_domain
from ecalx.experiments.contracts import (CreateVirtualInput, ConfigureOptimizationInput,
    AdvanceOptimizationInput, StrategyParameters, ImportRealInput, RequestModelUpdateInput, ExperimentError)
from ecalx.experiments.strategies import SelectionInput, select_next_candidate


class ReadOnlyEvidence(EvidenceRepository):
    def __init__(self, path):
        self.path = Path(path)
    def _connect(self):
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)


def scope_check(task, args, root):
    """Confirmed research goals cannot disappear from a simulator proposal."""
    from ecalx.domain.constraints import DiscoveryRequest
    from ecalx.services.discovery import unmet_constraints
    from ecalx.data.schema import EvidenceRecord
    goals = DiscoveryRequest(**task['plan']['goals'])
    if goals.material_type and goals.material_type not in {'陶瓷','bulk ceramic','钙钛矿','perovskite_ABO3','ABO3'}:
        raise ExperimentError('当前虚拟环境不支持所要求的材料类别；可继续无性能承诺的材料研究')
    if goals.delta_t_K is not None:
        raise ExperimentError('模拟值不能证明真实ΔT目标；请另建明确的演示任务')
    # Validate protocol against confirmed goals without inventing missing endpoints.
    row = EvidenceRecord(record_id='virtual-scope-check', formula_raw='BaTiO3',
        temperature_K=args.temperature_K, e_start_kV_cm=args.e_start_kV_cm,
        e_end_kV_cm=args.e_end_kV_cm)
    failures = [x for x in unmet_constraints(row, goals) if x not in {'必须包含元素','排除元素','材料类型'}]
    # Element restrictions must hold over the entire pool (not just the probe row).
    if failures:
        raise ExperimentError('模拟协议与已确认目标冲突：'+','.join(failures))
    if goals.include_elements or goals.exclude_elements:
        domain_elements = [{'Ba','Ti','O'}, {'Sr','Ti','O'}] if args.adapter=='SIMULATED' else [{'Ba','Sr','Mn','Ti','O'}]
        if any(set(goals.include_elements)-e or set(goals.exclude_elements)&e for e in domain_elements):
            raise ExperimentError('当前完整虚拟候选池不满足元素限制；不隐式删除目标')
    if args.adapter == 'HISTORICAL_REPLAY' and (args.temperature_K,args.e_start_kV_cm,args.e_end_kV_cm)!=(303,50,0):
        raise ExperimentError('历史协议仅303K、50→0；不能生成新历史标签')


class ScientificTools:
    def __init__(self, root, repository, virtual, real):
        self.root, self.repository, self.virtual, self.real = Path(root), repository, virtual, real

    def preview(self, name, args, task, key=None):
        if name in {'create_virtual_experiment','compare_optimization_strategies'}:
            scope_check(task,args,self.root)
            count = 2 if name=='compare_optimization_strategies' else 1
            domain=public_domain(args.adapter,self.root)
            return {'tool':name, 'parameters':args.model_dump(), 'source':SIMULATOR if args.adapter=='SIMULATED' else
                    {'source':'docs/bsmt_results/measurements.csv','scope':'12 exposed compositions / one paper'},
                    'strategies':['EI','Random'] if count==2 else [args.strategy],
                    'candidate_pool':domain, 'candidate_pool_size':len(domain),
                    'protocol_role':'EXPLICIT_VIRTUAL_PROPOSAL_REQUIRES_APPROVAL_NOT_USER_GOALS',
                    'feedback_provenance':args.adapter,
                    'budget':{'runs':count, 'initial_per_run':args.initial_count, 'additional_per_run':args.additional_budget,
                              'total_queries':count*(args.initial_count+args.additional_budget)},
                    'notice':'虚拟演示/历史回放，不是真实实验或已验证发现；审批后参数与预算冻结'}
        if name=='import_real_experiment':
            upload = self.upload(task,args.upload_id)
            if args.feedback_choice != upload['choice']:
                raise ExperimentError('工具不能改变用户上传时选择的反馈权限')
            return {'tool':name,'upload':{k:v for k,v in upload.items() if k!='path'}, 'training_started':False}
        if name=='request_model_update':
            self.owned(task,args.experiment_id,'real')
            return {'tool':name,'experiment_id':args.experiment_id,'action':'仅申请，SAVE_ONLY需独立新提交；之后仍需质量审核与单独准入授权','training_started':False}
        if name=='query_virtual_experiment':
            self.owned(task,args.experiment_id,'virtual')
            state=self.virtual.optimization_state(args.experiment_id)
            batch=self.virtual.optimization_request_progress(args.experiment_id,key) if key else None
            outstanding=args.rounds-(batch['completed'] if batch else 0)
            if outstanding > state['additional_budget']-state['consumed']:
                raise ExperimentError('超出已冻结预算')
            return {'tool':name,'parameters':args.model_dump(),'frozen_budget':state}
        return {}

    @staticmethod
    def owned(task,eid,kind):
        if eid not in task.get(kind+'_ids',[]):
            raise ExperimentError('实验不属于当前任务；禁止跨任务访问')

    @staticmethod
    def upload(task,uid):
        row=next((r for r in task['uploads'] if r['upload_id']==uid),None)
        if row is None:
            raise ExperimentError('没有用户主动提交的文件')
        return row

    def create(self, args, key, actor):
        lab=self.virtual
        token=lab.authorize('CREATE_VIRTUAL','new',actor,confirmed=True)
        params=args.model_dump(exclude={'additional_budget','strategy'})
        experiment=lab.create_virtual_experiment(CreateVirtualInput(**params, authorization_id=token), operation_key=key)
        eid=experiment.experiment_id
        token=lab.authorize('RUN_VIRTUAL_LOOP',eid,actor,confirmed=True)
        state=lab.configure_optimization(ConfigureOptimizationInput(experiment_id=eid,
            parameters=StrategyParameters(strategy=args.strategy), additional_budget=args.additional_budget, authorization_id=token))
        return {'experiment':experiment.model_dump(),'optimization':state}

    def execute(self, name, args, task, key):
        # Runtime calls preview BEFORE signing any lower-level authorization.
        actor='approved-agent-task:'+task['id']
        checkpoint=task['checkpoint']
        checkpoint()
        request=task['goals_request']
        if name=='search_material_evidence':
            result=discover(request,self.repository).to_dict()
            result['query_counts']={k:len(result[k]) for k in ('candidates','evidence_only','excluded')}
            result['count_semantics']='excluded仅指本次完整协议推荐准入失败；不表示原始实验无价值或被删除'
            # Keep a bounded local evidence view. Never treat document text as instructions.
            result['candidates']=result['candidates'][:24]
            result['evidence_only']=result['evidence_only'][:24]
            result['excluded']=result['excluded'][:24]
            return result
        if name=='generate_material_candidates':
            return explore_materials(request,self.repository,max_candidates=args.max_candidates)
        if name=='validate_chemical_constraints':
            amounts=parse_formula(args.formula)
            normalized, sites, states=REGISTRY.rules[args.family].validate(amounts)
            if set(request.exclude_elements)&amounts.keys() or set(request.include_elements)-amounts.keys():
                raise ExperimentError('违反已确认元素限制')
            return {'formula':args.formula,'family':args.family,'exact':{e:str(v) for e,v in normalized.items()},
                    'sites':sites,'assumed_states':states,'chemical_pass':True,'structure_verified':False,'performance':'unknown'}
        if name=='check_model_applicability':
            try:
                scope_check(task,args,self.root)
                identity=exact_key(parse_formula(args.formula))
                supported=any(r['identity']==identity for r in public_domain(args.adapter,self.root))
                reason='exact_registered_virtual_composition' if supported else 'composition_outside_frozen_virtual_domain'
            except (ValueError,KeyError) as error:
                supported,reason=False,str(error)
            return {'supported':supported,'reason':reason,'adapter':args.adapter,'quantitative_delta_T':None,
                    'production_model':'NOT_TRAINED','research_model':'TRAINED_UNVERIFIED',
                    'notice':'这里只检查虚拟定义域，未授予原研究模型定量预测权限；M5禁用'}
        if name=='create_virtual_experiment':
            return self.create(args,key,actor)
        if name=='select_next_candidate':
            self.owned(task,args.experiment_id,'virtual')
            report=self.virtual.export_report(args.experiment_id)
            state=report['optimization']; model=report['models'][-1]
            if state['consumed']>=state['additional_budget']:
                raise ExperimentError('预算已用尽')
            choice=select_next_candidate(SelectionInput(candidates=report['experiment']['candidates'],
                observations=report['observations'],predictions=model['predictions'],posterior_std=model['posterior_std'],
                parameters=StrategyParameters(**state['parameters']),seed=report['experiment']['seed'],round=state['consumed']+1))
            return choice.model_dump()
        if name=='query_virtual_experiment':
            token=self.virtual.authorize('RUN_VIRTUAL_LOOP',args.experiment_id,actor,confirmed=True)
            self.virtual.advance_optimization(AdvanceOptimizationInput(**args.model_dump(),request_id=key,authorization_id=token),checkpoint=checkpoint)
            return self.virtual.export_report(args.experiment_id)
        if name=='compare_optimization_strategies':
            # Stable receipts recover every run after process interruption; no oracle evaluator is returned to LLM.
            runs=[]
            for strategy in ('EI','Random'):
                checkpoint()
                params=args.model_copy(update={'strategy':strategy})
                created=self.create(params,key+':'+strategy,actor)
                eid=created['experiment']['experiment_id']
                token=self.virtual.authorize('RUN_VIRTUAL_LOOP',eid,actor,confirmed=True)
                self.virtual.advance_optimization(AdvanceOptimizationInput(experiment_id=eid,rounds=args.additional_budget,
                    request_id=key+':rounds',authorization_id=token),checkpoint=checkpoint)
                runs.append(self.virtual.export_report(eid))
            environments=[r['experiment']['conditions'] for r in runs]
            if environments[0]!=environments[1]:
                raise ExperimentError('比较环境不一致')
            return {'strategies':['EI','Random'],'runs':runs,'plan':args.model_dump(),
                'summary':[{'strategy':s,'completed_rounds':r['loop']['completed_rounds'],
                            'observations':len(r['observations']),'best_observed_K':max(o['value'] for o in r['observations'])}
                           for s,r in zip(('EI','Random'),runs)],
                'scope':'single frozen toy/history environment, descriptive only; not independent scientific advantage'}
        if name=='import_real_experiment':
            upload=self.upload(task,args.upload_id)
            content=Path(upload['path']).read_bytes() if args.feedback_choice!='SKIP_FEEDBACK' else None
            import hashlib
            if content is not None and hashlib.sha256(content).hexdigest()!=upload['sha256']:
                raise ExperimentError('上传文件SHA不符')
            token=self.real.authorize('IMPORT_REAL','new',actor,confirmed=True)
            result=self.real.import_real_experiment(ImportRealInput(filename=upload['filename'],feedback_choice=args.feedback_choice,
                authorization_id=token),content,operation_key=key)
            return result.model_dump()
        if name=='request_model_update':
            eid=args.experiment_id
            row=next(r for r in self.real.list_experiments() if r['experiment_id']==eid)
            # Never mutate SAVE_ONLY. Human-approved promotion creates a linked, immutable new submission.
            if row['feedback_choice']=='SAVE_ONLY':
                token=self.real.authorize('IMPORT_REAL','new',actor,confirmed=True)
                result=self.real.import_real_experiment(ImportRealInput(filename=row['metadata']['filename'],
                    feedback_choice='REQUEST_MODEL_UPDATE',authorization_id=token), self.real.raw_bytes(eid),operation_key=key+':new-submission')
                return {**result.model_dump(),'original_save_only_experiment_id':eid,'training_started':False}
            token=self.real.authorize('REQUEST_MODEL_UPDATE',eid,actor,confirmed=True)
            return self.real.request_model_update(RequestModelUpdateInput(experiment_id=eid,authorization_id=token)).model_dump()
        if name=='generate_research_report':
            return {'original':task['original'],'confirmed_goals':task['plan']['goals'],
                    'data_version':self.repository.data_version,'results':[{'call_id':s['id'],'tool':s['name'],'output':s.get('output')} for s in task['calls'] if s.get('output')],
                    'notice':'科研数值仅来自上述工具；新组成性能未知，虚拟结果非真实材料发现；M5禁用'}
        raise ExperimentError('未注册工具')
