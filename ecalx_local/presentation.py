"""User reports from actual receipts only, never provider narrative."""
import json
from .titles import research_title
from . import PERSONAL
from urllib.parse import quote
from .inputs import goals_summary
from .field_protocol import protocol_lines, imported_protocol
from ecalx.agent.faithfulness import source_ledger
from .conditions import receipt_conditions, summary_lines, coverage_text, ZERO_NOTICE, evidence_notice

TOOL_LABELS = {'search_material_evidence':'检索本地文献证据', 'generate_material_candidates':'探索候选组成',
    'validate_chemical_constraints':'验证候选组成', 'check_model_applicability':'检查模拟条件',
    'create_virtual_experiment':'创建虚拟实验', 'compare_optimization_strategies':'比较虚拟优化策略',
    'select_next_candidate':'选择下一虚拟查询', 'query_virtual_experiment':'运行虚拟查询',
    'generate_research_report':'整理研究报告', 'finish_research':'核验报告来源'}
STATUSES = {'DRAFT':'正在准备研究计划', 'RUNNING':'研究进行中', 'AWAITING_APPROVAL':'等待你的确认',
    'NEEDS_CLARIFICATION':'需要补充条件', 'PAUSED':'已暂停', 'COMPLETED':'候选探索完成，性能尚未验证',
    'CANCELLED':'已停止', 'FAILED':'任务已停止：执行未成功'}


def trusted(row):
    text = json.dumps(row, ensure_ascii=False).upper()
    return ('TEST_ONLY' not in text and 'SYNTHETIC' not in text) if PERSONAL else True


def candidate_cards(task):
    cards, seen = [], set()
    checks = {c['output']['data'].get('formula') for c in task['calls']
              if c['name']=='validate_chemical_constraints' and c['output']['status']=='OK'
              and c['output']['data'].get('chemical_pass')}
    for call in task['calls']:
        if call['name']!='generate_material_candidates' or call['output']['status']!='OK':
            continue
        for c in call['output']['data'].get('candidates', []):
            if c['candidate_id'] in seen or not trusted(c):
                continue
            seen.add(c['candidate_id'])
            sources = [r for s in c.get('generation_sources', []) for r in s.get('seed_sources', [])]
            cards.append({'id':c['candidate_id'], 'formula':c['formula_normalized'],
                'seed_formula':c.get('generation_sources', [{}])[0].get('seed_formula', c['seed_id']),
                'status':'generated_unverified', 'prediction':None, 'prediction_unit':'K', 'model_version':None,
                'reason': ('由真实文献种子 ' if PERSONAL else '由用户提供的来源种子（未独立核验；SYNTHETIC_TEST_DATA仅软件测试） ') + c.get('generation_sources', [{}])[0].get('seed_formula', c['seed_id']) + ' 的组成邻域生成；尚不能判断性能是否改善。',
                'chemical_check': any(c.get(k) in checks for k in ('formula','formula_raw','formula_normalized','formula_empirical')),
                'sources':sources, 'goals':c.get('goal_assessment', {}),
                'seed_coverage': c.get('design_basis', {}).get('seed_coverage', {}),
                'condition_warning': evidence_notice(receipt_conditions(task)) if receipt_conditions(task) is not None and receipt_conditions(task)['all_goal_matches']==0
                    else (('种子无严格路径联合命中，但有幅度匹配、路径未确认辅助证据；本候选性能仍未知。' if c.get('design_basis', {}).get('seed_coverage', {}).get('amplitude_only_auxiliary_matches',0) else '种子在目标条件下无联合命中；本候选性能仍未知。') if c.get('design_basis', {}).get('seed_coverage', {}).get('joint_temperature_field_hits')==0 else None),
                'gaps':'结构、实际价态、相稳定性、可制备性及目标条件下电卡性能仍需验证。'})
    return cards


def evidence_rows(task):
    rows, seen = [], set()
    for call in task['calls']:
        if call['name']!='search_material_evidence' or call['output']['status']!='OK':
            continue
        data = call['output']['data']
        for item in data.get('candidates', []) + data.get('evidence_only', []):
            r = item['evidence']
            if r['record_id'] in seen or not trusted(r):
                continue
            seen.add(r['record_id'])
            rows.append({**r, 'presentation_status': item['status'], 'match_scope':item.get('match_scope','legacy'), 'match_warning':item.get('warning',''), 'data_version':data['data_version']})
    return rows


def protocol_section(task):
    calls = [c for c in task['calls'] if c['name']=='search_material_evidence' and c['output']['status']=='OK']
    return calls[-1]['output']['data'].get('protocol_evidence') if calls else None


def doi_link(doi):
    return 'https://doi.org/' + quote(doi or '', safe='/.:')


def report_markdown(task):
    cards, rows = candidate_cards(task), evidence_rows(task)
    lines = ['# ECalX 材料研究报告', '', STATUSES[task['status']], '',
        '执行方式：' + ('DeepSeek 在线 Agent' if task['client_mode']=='LIVE_LLM' else '本地确定性科研工具流程（未调用 LLM）'),
        '', '## 研究需求', '', research_title(task), '',
        *goals_summary(task['frozen_goals']), *protocol_lines(task.get('input_snapshot',{}).get('field_protocol')), '', '## 研究结果', '',
        f'本次展示 {len(cards)} 个未验证组成候选、{len(rows)} 条本地文献记录。候选顺序为研究队列，不是性能排名。',
        '', '定量性能预测：unknown / 无可靠预测。模型版本：无获准模型。', '',
        ('已有模型为 TRAINED_UNVERIFIED，缺少独立外测、场路径统一和不确定性校准。' if PERSONAL else '公开模式没有装载预测模型。用户标签仅作证据展示，不会自动启用预测。SYNTHETIC_TEST_DATA 为原创软件测试数据，不是文献实验结果。'),
        '新组成为 generated_unverified；M5 正式科研推荐保持禁用。ΔT unknown；ΔS unknown。',
        '', '## 条件如何影响证据与探索', '', *summary_lines(receipt_conditions(task)),
        '生成器另读全库，温度/电场仅影响种子优先级；候选比例来自化学规则网格，没有计算目标条件下的物性。']
    if task.get('input_snapshot', {}).get('pending'):
        lines += ['', '保留为未量化文本（不作为硬筛选条件）：', *task['input_snapshot']['pending']]
    for c in cards:
        lines += ['', '### ' + c['formula'], '', '身份：generated_unverified；ΔT 预测：unknown K（无可靠预测）。',
                  c['reason'], '独立化学校验：' + ('通过；不代表结构或性能验证。' if c['chemical_check'] else '尚未执行。'), c['gaps']]
        lines += [coverage_text(c['seed_coverage']), 'ΔS：unknown；种子条件覆盖不等于本候选已达标。']
        if c['condition_warning']:
            lines += [c['condition_warning']]
        for r in c['sources']:
            lines += [f"- 来源：{r['record_id']}，[{r.get('doi') or r.get('url') or '无来源'}]({doi_link(r['doi']) if r.get('doi') else r.get('url') or ''})，{r.get('original_row')}。种子证据不是本候选的实测结果。"]
    lines += ['', '## 本地文献证据', '', '逐条保留温度、电场协议和测量方法；不同协议不混排。']
    for r in rows:
        def value(key, unit):
            return '未知' if r.get(key) is None else f"{r[key]:g} {unit}"
        lines += ['', f"- {r['formula_normalized']} · {r['record_id']} · {r['presentation_status']}",
            '  匹配解释：'+r.get('match_warning',''),
            f"  原文报告/换算 ΔT：{value('delta_T_ad_K','K')}；方法：{r['measurement_method']}；温度：{value('temperature_K','K')}；"
            f"电场：{value('e_start_kV_cm','kV/cm')} → {value('e_end_kV_cm','kV/cm')}；报告场：{value('e_reported_kV_cm','kV/cm')}（{r.get('e_reported_kind')}）。",
            f"  独立字段：ΔE {value('e_delta_kV_cm','kV/cm')}；电场上限 {value('e_max_kV_cm','kV/cm')}。",
            f"  协议状态：UNKNOWN / 上传字段未独立核验；提供的实际峰值：{imported_protocol(r)['peak_kV_cm']} kV/cm。",
            f"  ΔT 口径：{r.get('delta_t_basis')}；符号：{r.get('delta_t_sign_convention')}；形态：{r.get('sample_form')}。",
            f"  来源：[{r.get('doi') or r.get('url') or '无来源'}]({doi_link(r['doi']) if r.get('doi') else r.get('url') or ''})；{r['original_row']}；数据版本：{r['data_version']}。",
            '  限制：' + '、'.join(r.get('quality_flags', []))]
        for key, label, unit in [('delta_T_jump_abs_K','器件温跃幅值 |ΔTj|','K'),
                                 ('delta_S_vol_abs_kJ_K_m3','体积熵变幅值 |ΔS|','kJ/(K·m³)')]:
            if r.get(key) is not None:
                lines += [f"  {label}：{value(key,unit)}；器件：{r.get('specimen_id')}；派生方式：{r.get('value_derivation')}；归一化：{r.get('normalization_basis')}。",
                          f"  原Excel出处：{r.get('source_workbook')} / {r.get('source_sheet')} / {r.get('temperature_cell')}, {r.get('response_cell')}。仅作实验资料，不参与性能排名。"]
    calls = [c for c in task['calls'] if c['name']=='search_material_evidence' and c['output']['status']=='OK']
    if calls:
        from .evidence_display import statistics_lines
        lines += statistics_lines(calls[-1]['output']['data'])
    if not cards and not rows:
        lines += ['', '当前未取得可展示的候选或匹配证据。没有补造组成或性能。']
    protocol = protocol_section(task)
    if protocol:
        lines += ['', '## 独立协议证据区（不并入默认库计数）', '', protocol['notice']]
        if protocol['status']=='verified_readonly':
            lines += ['已核实实验协议 · OBSERVED（仅下列核验记录与字段）。']
            lines += [f"核验记录 {len(protocol['records'])} 条 / {protocol['papers']} 篇论文；全部已指定条件匹配 {protocol['matching_records']} 条。"]
            for r in protocol['records']:
                f = r['verified_fields']
                lines += [f"- {r['formula']} · {r['record_id']} · 正温降幅值 {r['positive_drop_magnitude_K']:g} K；"
                    f"{r['temperature_K']:g} K；{f['e_start_kV_cm']:g}→{f['e_end_kV_cm']:g} kV/cm；"
                    f"目标条件：{'匹配测试点' if not r['unmet_conditions'] else '不匹配 '+','.join(r['unmet_conditions'])}；"
                    f"[{r.get('doi') or r.get('url') or '无来源'}]({doi_link(r['doi']) if r.get('doi') else r.get('url') or ''})；{r['original_row']}；带符号ΔT未知。"]
    virtual = [c for c in task['calls'] if 'virtual' in c['name'] or c['name']=='compare_optimization_strategies']
    if virtual:
        lines += ['', '## 虚拟实验', '', '本次实际执行了虚拟工具；模拟/历史标签不构成新材料实测或可靠 EC 性能。数值仅对经确认的冻结虚拟环境有效。']
        for c in virtual:
            lines += ['- ' + TOOL_LABELS[c['name']] + '：' + c['output']['status']]
    lines += ['', '## 后续研究', '', '先核查原论文、样品及场路径，再对候选开展结构/合成和目标温区同协议测量。当前不能给出可信性能排名。',
              '', '## 执行摘要', '']
    for c in task['calls']:
        lines += ['- ' + TOOL_LABELS[c['name']] + '：' + c['output']['status']]
    if task['status']!='COMPLETED':
        lines += ['', '这是未完成任务的阶段报告，不代表研究成功完成。']
    return '\n'.join(lines) + '\n'
