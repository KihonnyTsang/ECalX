"""Element-constrained orchestration of existing chemistry and M4 tools.

No performance model, seed formula, or candidate formula is supplied by this UI.
"""
from collections import defaultdict
from fractions import Fraction
import json

from ecalx.materials.chemistry import parse_formula, exact_key, composition_key
from ecalx.materials.generator import REGISTRY, GenerationRequest, generate, material_display
from ecalx.services.automatic import reference, source_coverage
from ecalx.experiments.candidate_pool import generated_candidate_pool
from ecalx.experiments.virtual import VirtualLaboratory
from ecalx.experiments.contracts import (CreateVirtualInput, ConfigureOptimizationInput,
    AdvanceOptimizationInput, StrategyParameters)

GRID = tuple(Fraction(i,20) for i in range(1,6))


def generate_from_elements(request, repository, allowed, limit=10):
    """Select the simplest traceable seed, enumerate compatible allowed dopants.

    Finite engineering grid, not a solubility claim or a new scientific model.
    """
    allowed=set(allowed)
    groups=defaultdict(list)
    known=defaultdict(list)
    for r in repository.records():
        if not (r.doi or r.url) or 'quarantined' in r.quality_flags or getattr(r,'experimental_response',False):
            continue
        try:
            amounts=parse_formula(r.formula_normalized or r.formula_raw)
            if not set(amounts)<=allowed or set(amounts)&set(request.exclude_elements):
                continue
            known[composition_key(amounts)].append(reference(r))
            for family,rule in REGISTRY.rules.items():
                try:
                    rule.validate(amounts)
                except ValueError:
                    continue
                if request.material_type and request.material_type not in {family,'钙钛矿' if family=='perovskite_ABO3' else family} and request.material_type!=r.sample_form:
                    continue
                groups[(family,exact_key(amounts))].append(r)
        except ValueError:
            continue
    result={'schema_version':'element-search-1','status':'RESEARCH_ONLY',
        'data_version':repository.data_version,'request':request.to_dict(),'allowed_elements':sorted(allowed),
        'search_budget':{'max_candidates':limit,'host_fraction_grid':list(map(str,GRID)),
                         'interpretation':'engineering enumeration only; not verified solubility'},
        'seeds':[],'candidates':[],'rejected':[], 'predictions':[],
        'production_recommendation_enabled':False,'M5_enabled':False,
        'ordering':'chemical_queue_no_performance_ranking'}
    seen=set()
    groups=sorted(groups.items(),key=lambda item:(len(parse_formula(item[1][0].formula_normalized or item[1][0].formula_raw)), -len(item[1]),item[0]))
    for (family,_),rows in groups:
        seed_record=sorted(rows,key=lambda r:r.record_id)[0]
        rule=REGISTRY.rules[family]
        seed,sites,_=rule.validate(parse_formula(seed_record.formula_normalized or seed_record.formula_raw))
        sources=list({r.doi or r.url:reference(r) for r in reversed(sorted(rows,key=lambda r:r.record_id))}.values())
        plans=[]
        # Interleave dopants at each grid step; avoid selecting by hidden labels.
        for x in GRID:
            for site,states in rule.sites.items():
                for host in sorted(seed.keys() & states.keys()):
                    for dopant in sorted(allowed & states.keys()):
                        if host!=dopant and states[host]==states[dopant] and dopant not in request.exclude_elements:
                            plans.append([{'host':host,'dopant':dopant,'fraction':str(x)}])
        seed_formula=material_display(seed,family)['formula_normalized']
        generated=generate(seed_formula,sources,family,plans,GenerationRequest(
            include=request.include_elements,exclude=request.exclude_elements,material_type=request.material_type),known=known)
        # generate() orders by identity; restore the declared parameter queue.
        order={json.dumps(plan,sort_keys=True):i for i,plan in enumerate(plans)}
        candidates=sorted(generated['candidates'],key=lambda c:order[json.dumps(c['generation_sources'][0]['parameters'],sort_keys=True)])
        coverage=source_coverage(rows,request)
        if candidates:
            result['seeds'].append({'seed_id':seed_record.record_id,'formula_raw':seed_formula,
                'family_rule':family,'sources':sources,'coverage':coverage})
        for c in candidates:
            if not set(c['stoichiometry_exact'])<=allowed or c['candidate_id'] in seen:
                continue
            seen.add(c['candidate_id'])
            c.update(seed_id=seed_record.record_id,seed_formula_raw=seed_formula,
                chemical_checks={'stoichiometry':'pass','elements':'pass','sites':'pass','assumed_charge_balance':'pass','structure_confirmed':False},
                design_basis={'seed_coverage':coverage},
                goal_assessment={'elements':'satisfied_under_nominal_formula'},
                research_priority={'basis':{'seed_experimental_coverage':coverage}})
            result['candidates'].append(c)
            if len(result['candidates'])==limit:
                break
        result['rejected']+=generated['rejected']
        if len(result['candidates'])==limit:
            break
    result['message']=f"程序枚举 {len(result['candidates'])} 个候选；化学规则通过，结构与性能待验证。"
    return result


def generation_result(task):
    return next((c['output']['data'] for c in reversed(task['calls'])
        if c['name']=='generate_material_candidates' and c['output']['status']=='OK'),{'candidates':[]})


def attach_run(agent,tid,run):
    # Update only this field, preserving concurrent task updates and prior runs.
    with agent.connect() as db:
        task=json.loads(db.execute('SELECT payload FROM research_tasks WHERE id=?',(tid,)).fetchone()[0])
        task.setdefault('simulation_runs',[])
        task['simulation_runs']=[r for r in task['simulation_runs'] if r['experiment_id']!=run['experiment_id']]+[run]
        db.execute('UPDATE research_tasks SET payload=? WHERE id=?',(json.dumps(task,ensure_ascii=False),tid))


def create_simulation(agent, task, temperature, start, end):
    from ecalx.agent.tools import scope_check
    from types import SimpleNamespace
    scope_check({'plan':{'goals':task['frozen_goals']}},SimpleNamespace(
        adapter='SIMULATED',temperature_K=temperature,e_start_kV_cm=start,e_end_kV_cm=end),agent.root)
    generation=generation_result(task)
    bridge=generated_candidate_pool(generation,'SIMULATED',agent.root)
    if len(bridge['candidate_keys'])<3:
        raise ValueError('至少需要3个落在模拟器网格上的已生成候选，才能演示逐轮优化。')
    lab=VirtualLaboratory(agent.storage_root,agent.root)
    grant=lab.authorize('CREATE_VIRTUAL','new','user-requested-local-demo',confirmed=True)
    experiment=lab.create_virtual_experiment(CreateVirtualInput(adapter='SIMULATED',
        candidate_keys=bridge['candidate_keys'],initial_count=2,seed=42,
        temperature_K=temperature,e_start_kV_cm=start,e_end_kV_cm=end,authorization_id=grant))
    run={'experiment_id':experiment.experiment_id,'bridge':bridge,
         'parameter_scope':'SIMULATOR_SETTINGS_NOT_USER_GOALS',
         'research_goals_unchanged':True,'report':lab.export_report(experiment.experiment_id)}
    attach_run(agent,task['id'],run)
    agent.log(task['id'],'LOCAL_SIMULATION_CREATED',{'experiment_id':experiment.experiment_id,'bridge':bridge})
    return run


def advance_simulation(agent,task,run,all_remaining=False):
    lab=VirtualLaboratory(agent.storage_root,agent.root)
    eid=run['experiment_id']
    report=lab.export_report(eid)
    grant=lab.authorize('RUN_VIRTUAL_LOOP',eid,'user-requested-local-demo',confirmed=True)
    budget=len(report['experiment']['candidates'])-len(report['experiment']['initial_feedback'])
    plan=lab.configure_optimization(ConfigureOptimizationInput(experiment_id=eid,
        parameters=StrategyParameters(strategy='EI'),additional_budget=budget,authorization_id=grant))
    remaining=budget-plan['consumed']
    if remaining:
        lab.advance_optimization(AdvanceOptimizationInput(experiment_id=eid,rounds=remaining if all_remaining else 1,
            request_id=f"ui-{plan['consumed']}-{'all' if all_remaining else 'one'}",authorization_id=grant))
    run={**run,'report':lab.export_report(eid)}
    attach_run(agent,task['id'],run)
    return run


def ranking(run):
    report=run['report']; formulas={c['candidate_id']:c['formula'] for c in report['experiment']['candidates']}
    observed={r['candidate_id']:r for r in report['observations']}
    generated={r['candidate_key']:r['generated_candidate_id'] for r in run['bridge']['accepted']}
    # Same simulator settings within a run. No physical-property inference.
    ranked=sorted(observed.values(),key=lambda r:(-r['value'],r['candidate_id']))
    return [{'名次':i+1,'候选组分':formulas[r['candidate_id']],'模拟评分':r['value'],
        '状态':'SIMULATED · 待实验验证','candidate_id':generated[r['candidate_id'].rsplit(':',1)[-1]]}
        for i,r in enumerate(ranked)]


def report_section(task):
    allowed=task.get('input_snapshot',{}).get('allowed_elements',[])
    lines=['','## 元素条件与计算范围','','允许元素：'+('、'.join(allowed) or '未指定'),
        '生成网格：5%–25% 同价位点替代，仅为工程搜索范围，不代表已验证固溶范围。'] if allowed else []
    for run in task.get('simulation_runs',[]):
        report=run['report'];p=report['experiment']['conditions'];rows=ranking(run)
        lines+=['','## M4 虚拟实验与候选筛选','','SIMULATED · 未校准演示模型，评分不是实测电卡 ΔT。',
            f"模拟器参数：{p['temperature_K']:g} K，{p['e_start_kV_cm']:g}→{p['e_end_kV_cm']:g} kV/cm；仅为计算设置，不是新增研究目标。",
            f"实验 ID：{run['experiment_id']}；环境 SHA256：{report['environment_sha256']}。",
            f"程序生成 {len(generation_result(task)['candidates'])} 个候选，{len(run['bridge']['accepted'])} 个可模拟，{len(run['bridge']['rejected'])} 个域外拒绝。",
            f"EI 优化已运行 {report['loop']['completed_rounds']} 轮；已揭示 {len(rows)} 个候选。每轮仅根据已观测模拟反馈重新拟合固定 RBF 高斯过程。",
            '排序依据：同一冻结环境下的模拟反馈值降序；未查询或域外候选不参与。',
            '','| 名次 | 组成 | 模拟评分 |','| --- | --- | --- |']
        lines += [f"| {r['名次']} | {r['候选组分']} | {r['模拟评分']:.6f} |" for r in rows]
        if rows:
            lines += ['',f"模拟评分最高的候选组分：{rows[0]['候选组分']}。可优先进行同协议实测与结构核查；真实电卡性能优劣未知。"]
        lines += ['','计算依据：`maxwell-toy-1` / `ecalx.experiments.adapters`；评分参数未经 BST 实测校准。',
            '未使用 BST 文献插值器、LGD 或正式机器学习预测；不提供 ΔS、模型精度或真实最优结论。']
    return '\n'.join(lines)
