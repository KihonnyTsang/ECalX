"""Continuous, receipt-grounded research. Parent tasks are read through SQLite RO.

The provider selects host-rendered fact blocks; free provider prose never becomes
scientific evidence. Turns live in a sidecar, not in the historical task payload.
"""
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from . import APP_ROOT, SCIENCE_ROOT, PERSONAL, STORAGE
from .conditions import receipt_conditions, summary_lines, coverage_text, amplitude_summary_lines
from .inputs import goals_summary
from .field_protocol import CUSTOM, UNKNOWN, reference_protocol, protocol_lines
from .presentation import candidate_cards, doi_link
from ecalx.agent.contracts import Goals
from ecalx.agent.llm import ProviderError, redact
from ecalx.parsing.requirements import parse_request


BOUNDARY = '候选仍待验证，正式 ΔT / ΔS 为 unknown；以下解释不构成性能预测或材料排名。'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def estimate_goals(receipt):
    value = receipt.get('confirmed_goals')
    return {k:value.get(k) for k in Goals.model_fields} if isinstance(value,dict) else None


def read_task(tid, storage=None):
    if not re.fullmatch(r'task-[a-f0-9]{32}', tid):
        raise ValueError('无效研究记录')
    path = Path(storage or STORAGE)/'tasks.sqlite3'
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT payload FROM research_tasks WHERE id=?', (tid,)).fetchone()
    if not row:
        raise ValueError('研究记录不存在')
    return json.loads(row[0])


def is_condition_change(question):
    """Conservative route, never permission to mutate or an exhaustive NL parser."""
    if re.search(r'(?:为什么|为何).*?(?:不能|不应|不允许)(?:修改|更改|调整)|不要(?:修改|更改|调整)', question):
        return False
    action = r'改成|改为|改到|改一下|修改|更改|调整|换成|换为|设为|设置为|取消|去掉|不再|新增|增加|放宽|收紧|提高|降低|升到|降到|升至|降至|重新研究|重新筛选|重新生成|换个|改用|改研究|改温|改电场|改条件'
    domain = r'温|电场|场强|条件|约束|材料|元素|组成|配方|无铅|含铅|排除|包含|ΔE|ΔT|℃|°C|kV/|MV/|陶瓷|薄膜|聚合物'
    if re.search(action, question, re.I) and re.search(domain, question, re.I):
        return True
    if re.search(r'^(?:请)?(?:排除|必须含|必须包含|只考虑|不含|包含)', question.strip()):
        return True
    # A bare new condition is routed to a draft. Explanatory numeric questions stay Q&A.
    explanatory = re.search(r'为什么|为何|依据|证据|解释|意味着|含义|支持|如何|怎么|误差|估计|能否|是否|比较|原来|之前|当前', question)
    return bool(not explanatory and re.search(r'\d\s*(?:°C|℃|K\b|kV/cm|kV/mm|MV/m)|材料类型\s*[:：]', question))


def change_draft(task, question):
    goals = deepcopy(task['frozen_goals'])
    notes = []
    try:
        parse_text = re.sub(r'(上限|下限|变化幅度)\s*(?:改为|改成|设为|调整为)', r'\1', question)
        parsed = parse_request({'natural_language': parse_text})
        patch = Goals(**{k:v for k,v in parsed.request.to_dict().items() if k in Goals.model_fields}).model_dump()
        for key, value in patch.items():
            if value not in (None, False, [], ''):
                if key in ('include_elements', 'exclude_elements'):
                    goals[key] = sorted(set(goals[key]) | set(value))
                else:
                    goals[key] = value
        # An explicit field mode replaces the other old field representations.
        field_keys = ('e_start_kV_cm', 'e_end_kV_cm', 'e_min_kV_cm', 'e_max_kV_cm', 'delta_E_kV_cm')
        changed_fields = {k for k in field_keys if patch[k] is not None}
        if changed_fields:
            for k in field_keys:
                goals[k] = patch[k]
        notes = list(parsed.pending)
    except ValueError:
        notes = ['自然语言条件未能完整解析，请在下方编辑；未自动应用。']
    if re.search(r'取消|去掉|不再|放宽', question):
        # Clearing constraints is always explicit in the full editor.
        notes.append('取消或放宽的条件请在下方清空；不会自动猜测取消范围。')
    return {'parent_task_id': task['id'], 'parent_sha256': fingerprint(task),
            'question': question, 'goals': goals, 'notes': notes}


def prepare_child(task, question, goals):
    value = Goals.model_validate(goals, strict=True).model_dump()
    if value == task['frozen_goals']:
        raise ValueError('新条件与原研究相同；请先编辑需要改变的条件。')
    protocol = task.get('input_snapshot',{}).get('field_protocol')
    if not protocol or any(value[k] != task['frozen_goals'][k] for k in ('e_start_kV_cm','e_end_kV_cm','delta_E_kV_cm')):
        protocol = reference_protocol(CUSTOM if any(value[k] is not None for k in ('e_start_kV_cm','e_end_kV_cm')) else UNKNOWN,
            start=value['e_start_kV_cm'],end=value['e_end_kV_cm'],delta=value['delta_E_kV_cm'])
    return {'raw': {'natural_language': question}, 'goals': value,
            'summary': goals_summary(value) + protocol_lines(protocol), 'field_protocol': protocol, 'pending': [],
            'parent_task_id': task['id'], 'parent_sha256': fingerprint(task)}


def generated(task):
    return [(call, c) for call in task['calls']
            if call['name']=='generate_material_candidates' and call['output']['status']=='OK'
            for c in call['output']['data'].get('candidates', [])]


def choose_candidates(task, question, selected=(), history=()):
    candidates = [c for _, c in generated(task)]
    known = {c['candidate_id'] for c in candidates}
    if set(selected)-known or len(selected)>2:
        raise ValueError('只能选择本任务中最多两个候选')
    if selected:
        return list(selected)
    found = []
    for c in candidates:
        aliases = (c['candidate_id'], c.get('formula_raw'), c.get('formula_normalized'), c.get('formula_empirical'))
        if any(alias and alias in question for alias in aliases):
            found.append(c['candidate_id'])
    for match in re.finditer(r'第([一二三四1234])(?:个|种|项)', question):
        i = '一二三四'.index(match[1]) if match[1] in '一二三四' else int(match[1])-1
        if i < len(candidates):
            found.append(candidates[i]['candidate_id'])
    if not found and re.search(r'它|这个|这两个|刚才|上述|上一个|继续', question):
        previous = next((h for h in reversed(history) if h.get('selected_candidates')), None)
        if previous:
            found = list(previous['selected_candidates'])
    return list(dict.fromkeys(found))[:2]


def estimate_receipts(task, directory=None):
    """Only explicitly linked receipts; old unlinked/demo files cannot attach by formula."""
    directory = Path(directory or (STORAGE.parent/'exploratory' if PERSONAL else SCIENCE_ROOT/'exploratory'))
    if not PERSONAL:
        return []
    result = []
    for path in sorted(directory.glob('exploratory-*.json')):
        try:
            r = json.loads(path.read_text())
            if (r.get('parent_task_id') == task['id'] and not r.get('demo')
                    and r.get('parent_goals_sha256') == fingerprint(task['frozen_goals'])
                    and estimate_goals(r) == task['frozen_goals']
                    and r.get('receipt_id') == path.stem):
                result.append(r)
        except (OSError, ValueError):
            continue
    return result


def fact_catalog(task, *, estimates=()):
    """Chinese facts are derived exclusively from successful saved receipts."""
    blocks = {}
    def add(key, title, text, references=(), candidate_ids=()):
        blocks[key] = {'id': key, 'title': title, 'text': text,
                       'references': list(references), 'candidate_ids': list(candidate_ids)}
    calls = [c for c in task['calls'] if c['output']['status']=='OK']
    search = next((c for c in calls if c['name']=='search_material_evidence'), None)
    search_ref = [{'call_id': search['id'], 'kind':'saved_tool_receipt'}] if search else []
    add('overview', '这次研究做了什么', '\n'.join([
        task['original'] or '开放探索', *goals_summary(task['frozen_goals']),
        *protocol_lines(task.get('input_snapshot',{}).get('field_protocol')),
        f'已保存 {len(candidate_cards(task))} 个待验证候选；展示顺序是研究队列，不能解释为性能名次。', BOUNDARY]), search_ref)
    summary = receipt_conditions(task)
    add('gaps', '哪些条件缺少实验支持', '\n\n'.join([
        *summary_lines(summary)[:3],
        *(amplitude_summary_lines(summary) if summary else []),
        *protocol_lines(task.get('input_snapshot',{}).get('field_protocol')),
        ('已有文献记录缺少明确场起终点，不能把 ΔE 变化幅度当作电场上限或指定加载/撤场路径。' if summary and summary.get('missing_field_endpoints') else
         '只有协议明确且条件一致的记录，才能支持对应温度和场路径下的比较。'),
        '温度/电场覆盖属于文献种子；种子命中不能证明新候选达标，单点命中不能代表整个温区。',
        '没有每个候选的目标条件实测、独立最终外测或校准区间；化学计量检查不能验证实际结构、合成和性能。',
        '这里只解释保存的收据，没有重新检索或用当前数据覆盖历史计数。']), search_ref)
    for call, c in generated(task):
        cid = c['candidate_id']
        sources = c.get('generation_sources', [])
        lines, refs = [], [{'call_id':call['id'], 'candidate_id':cid, 'kind':'generation_receipt'}]
        for s in sources:
            rule = {'perovskite_ABO3':'ABO₃ 钙钛矿位点','fluorite_AO2':'AO₂ 萤石位点',
                    'spinel_AB2O4':'AB₂O₄ 尖晶石位点'}.get(s['rule'],'已保存的位点')
            lines.append('文献种子：'+s['seed_formula']+'；采用 '+rule+'化学替代规则。')
            for param in s.get('parameters', []):
                fraction = Fraction(param['fraction'])
                lines.append(f"用 {param['dopant']} 替代种子中 {param['host']} 数量的 {float(fraction)*100:g}%（比例针对宿主量，不是所有原子）。")
            for r in s.get('seed_sources', []):
                refs.append({k:r.get(k) for k in ('record_id','doi','original_row','temperature_K',
                    'measurement_method','e_reported_kind','e_reported_kV_cm','e_start_kV_cm','e_end_kV_cm')})
                refs[-1].update(call_id=call['id'], kind='seed_reference')
                method = {'direct':'直接法','indirect':'间接法'}.get(r.get('measurement_method'), '方法未明确')
                field = {'delta':'变化幅度 ΔE','maximum':'场上限','applied':'施加场'}.get(r.get('e_reported_kind'), '场含义待核实')
                start = '未知' if r.get('e_start_kV_cm') is None else str(r['e_start_kV_cm'])
                end = '未知' if r.get('e_end_kV_cm') is None else str(r['e_end_kV_cm'])
                lines.append(f"种子来源采用{method}；记录温度 {r.get('temperature_K')} K，原报告场 {r.get('e_reported_kV_cm')} kV/cm（{field}）；起终点 {start} → {end}，缺失值表示未核实。")
        check = next((x for x in calls if x['name']=='validate_chemical_constraints'
                      and x['output']['data'].get('formula') in
                      (c.get('formula_raw'),c.get('formula_normalized'),c.get('formula_empirical'))), None)
        if check:
            refs.append({'call_id':check['id'], 'kind':'chemical_receipt'})
        passed = bool(check and check['output']['data'].get('chemical_pass'))
        lines += ['独立化学校验：'+('通过声明价态下的计量、位点与电荷检查。' if passed else '未取得通过收据。'),
            coverage_text(c.get('design_basis', {}).get('seed_coverage', {})),
            '生成比例是工程探索网格，没有实验确认连续固溶范围；没有证明替代后电卡性能会提升。',
            '结构、实际价态、制备批次、相稳定性及目标温度/电场下的性能仍缺验证。', BOUNDARY]
        add('candidate:'+cid, c['formula_normalized']+' 的生成依据', '\n\n'.join(lines), refs, [cid])
    add('comparison', '怎样比较候选', '可以比较种子来源、替代元素与比例、化学校验和条件证据覆盖。来源更多或化学校验通过，不等于 ΔT 更高；现有证据不支持选出性能最优者。')
    add('estimate_limits', '怎样理解研究性 ΔT 估计',
        '已有RF/XGBoost学习的是文献原报告ΔT标签；直接法与间接法分别建模。路径、符号、样品工艺与热损未统一。历史平均误差不是当前材料的误差条，没有独立最终外测或校准区间；数值不能升级为正式性能或自动排名。解释不会重新运行模型。')
    linked = [r for r in estimates if r.get('parent_task_id')==task['id']
              and r.get('parent_goals_sha256')==fingerprint(task['frozen_goals'])
              and estimate_goals(r)==task['frozen_goals'] and not r.get('demo')]
    for r in linked:
        metadata, inputs = r.get('metadata') or {}, r['inputs']
        text = f"保存的研究性估计：组成 {inputs['formula']}，评价点 {inputs.get('temperature_K')} K，ΔE {inputs.get('delta_E_kV_cm')} kV/cm；模型 {inputs.get('model')}，方法 {inputs.get('method')}。"
        if r['status']=='RESEARCH_ONLY':
            text += f"已有工件给出的探索值 {r['prediction_K']:.3f} K；正式 ΔT 仍 unknown。"
        else:
            text += '该收据未获数值输出，状态为 '+r['status']+'。'
        text += '没有重新推理；不能用该值判断候选已达标或更优。'
        add('estimate:'+r['receipt_id'], '已保存估计的含义', text,
            [{'kind':'exploratory_receipt','receipt_id':r['receipt_id'],
              'model_sha256':metadata.get('model_sha256')}])
    if not linked:
        add('no_estimate', '本任务尚无关联的估计收据',
            '没有找到明确关联本任务及冻结条件的探索性估计。不会把演示值或仅同名组成的其他任务收据当作本次结果。可在候选详情主动运行可选估计；当前追问不会自动推理。')
    add('unsupported', '当前记录能回答的范围',
        '这份记录没有足够证据回答该问题。可以解释候选生成、比较来源、核对条件缺口与已有估计；不能据此编造文献机制、候选实测性能、全球新颖性或可靠性能排名。请指出具体候选或需要核查的收据。')
    if not PERSONAL:
        blocks['estimate_limits']['text']='公开模式未装载任何预测模型；ΔT/ΔS 为 unknown。导入性能标签不会自动创建或启用预测模型。'
        blocks['no_estimate']['text']='公开模式不运行个人RF/XGBoost模型；没有适用域检查通过的用户模型收据。'
        for block in blocks.values():
            block['text']=block['text'].replace('文献种子：','用户来源种子（未经独立文献核验；SYNTHETIC_TEST_DATA仅软件测试）：')
    return blocks


def local_selection(question, selected):
    if re.search(r'比较|区别|差异|两种|两个|更好|最好|排名|排序|优于', question):
        return (['comparison'] + ['candidate:'+c for c in selected] + ['gaps']) if len(selected)==2 else ['comparison', 'unsupported']
    if re.search(r'估计|预测|RF|XGBoost|误差|置信|ΔT|温变', question, re.I):
        return ['estimate_limits', 'linked_estimates']
    if re.search(r'为什么|为何|生成|替代|掺杂|依据|来源', question) and selected:
        return ['candidate:'+c for c in selected] + ['gaps']
    if re.search(r'缺|支持|条件|实验|证据|验证|达标', question):
        return ['gaps'] + ['candidate:'+c for c in selected]
    if re.search(r'总结|概述|做了什么|结果|研究', question):
        return ['overview','gaps']
    return ['unsupported']


class Conversation:
    def __init__(self, storage=None):
        self.storage = Path(storage or STORAGE).resolve()
        if PERSONAL and self.storage.is_relative_to(SCIENCE_ROOT.resolve()):
            raise ValueError('追问必须保存在原科研工程之外')
        self.directory = self.storage/'conversations'

    def history(self, tid):
        read_task(tid, self.storage)
        return sorted([json.loads(p.read_text()) for p in (self.directory/tid).glob('turn-*.json')],
                      key=lambda t:(t['created_at'], t['id']))

    def create(self, tid, question, *, mode='local', selected=(), consent=None):
        if not isinstance(question, str) or not question.strip() or len(question)>2000:
            raise ValueError('追问请填写1–2000个字符')
        if mode not in {'local','live'}:
            raise ValueError('未知追问方式')
        task = read_task(tid, self.storage)
        if task['status']!='COMPLETED':
            raise ValueError('请在已完成研究上追问；未完成任务可查看阶段报告')
        question = redact(question.strip())
        selected = choose_candidates(task, question, selected, self.history(tid))
        changing = is_condition_change(question)
        if mode=='live' and not changing:
            from .conversation_live import followup_quote
            if consent != followup_quote():
                raise ValueError('请明确确认本次追问的当前预算与发送范围')
        turn = {'id':'turn-'+uuid4().hex, 'parent_task_id':tid,
                'parent_sha256':fingerprint(task), 'goals_sha256':fingerprint(task['frozen_goals']),
                'created_at':datetime.now(timezone.utc).isoformat(), 'question':question,
                'selected_candidates':selected, 'mode':'CONDITION_CHANGE' if changing else
                    'LOCAL_EVIDENCE_TOOLS' if mode=='local' else 'LIVE_LLM',
                'status':'NEEDS_NEW_TASK' if changing else 'PENDING',
                'consent':consent if mode=='live' and not changing else None,
                'receipts':[], 'block_ids':[], 'answer':'', 'provider_metrics':[],
                'online_acceptance':'NOT_RUN', 'paid_api_requests':0}
        if changing:
            turn['draft'] = change_draft(task, question)
            turn['answer'] = '这条消息涉及研究条件变更，需要创建新的研究任务。原研究的冻结条件、候选和历史保持不变。请在新任务草稿中核对全部条件后再创建。'
        folder = self.directory/tid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/(turn['id']+'.json')
        with path.open('x') as handle:
            json.dump(turn, handle, ensure_ascii=False, indent=2, allow_nan=False)
        if mode=='local' and not changing:
            return self.run(tid, turn['id'])
        return turn

    def get(self, tid, turn_id):
        read_task(tid, self.storage)
        if not re.fullmatch(r'turn-[a-f0-9]{32}', turn_id):
            raise ValueError('无效追问记录')
        turn = json.loads((self.directory/tid/(turn_id+'.json')).read_text())
        if turn['status']=='RUNNING' and turn.get('worker_pid'):
            try:
                os.kill(turn['worker_pid'], 0)
            except ProcessLookupError:
                turn.update(status='INTERRUPTED',
                    answer='追问进程已中断；已保留本轮收据，用量可能未知，不自动重发。原研究未改变。')
                self.save(turn)
            except PermissionError:
                pass
        return turn

    def save(self, turn):
        path = self.directory/turn['parent_task_id']/(turn['id']+'.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(redact(turn), ensure_ascii=False, indent=2, allow_nan=False))
        temp.replace(path)

    def run(self, tid, turn_id, client=None):
        folder = self.directory/tid
        with (folder/(turn_id+'.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return self.get(tid, turn_id)
            turn = self.get(tid, turn_id)
            if turn['status']!='PENDING':
                return turn  # no replay / paid retry
            task = read_task(tid, self.storage)
            if fingerprint(task)!=turn['parent_sha256']:
                turn.update(status='FAILED', answer='原研究快照发生变化，追问未执行。请重新打开研究记录。')
                self.save(turn)
                return turn
            turn['status']='RUNNING'
            turn['worker_pid']=os.getpid()
            self.save(turn)
            try:
                blocks = fact_catalog(task, estimates=estimate_receipts(task))
                if turn['mode']=='LIVE_LLM':
                    from .conversation_live import run_live
                    ids = run_live(self, task, turn, blocks, client)
                else:
                    ids = local_selection(turn['question'], turn['selected_candidates'])
                    if 'linked_estimates' in ids:
                        ids = [k for k in ids if k!='linked_estimates'] + ([k for k in blocks if k.startswith('estimate:')] or ['no_estimate'])
                    turn['receipts'].append({'tool':'read_saved_research_evidence',
                        'execution':'REAL_LOCAL_READONLY_TOOL', 'parent_sha256':fingerprint(task),
                        'block_ids':ids})
                if not ids or set(ids)-blocks.keys():
                    raise ValueError('回答引用了不存在的证据')
                if fingerprint(read_task(tid, self.storage))!=turn['parent_sha256']:
                    raise ValueError('研究快照已变化')
                turn.update(status='COMPLETED', block_ids=ids,
                    facts=[blocks[k] for k in ids],
                    answer='\n\n'.join('**'+blocks[k]['title']+'**\n\n'+blocks[k]['text'] for k in ids))
            except Exception as error:
                turn.update(status='FAILED', error_code=getattr(error,'code','EVIDENCE_CONTRACT_REJECTED'),
                    answer='本次追问未完成，未采用模型自由文本或生成替代性能结论。请查看高级详情；不会自动重试。')
            self.save(turn)
            return turn


def answer_markdown(turn):
    text = turn['answer']
    refs = {r['doi'] for b in turn.get('facts', []) for r in b['references'] if r.get('doi')}
    if refs:
        text += '\n\n来源：'+'；'.join('[论文 '+d+']('+doi_link(d)+')' for d in sorted(refs))
    return text
