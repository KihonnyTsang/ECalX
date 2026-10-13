"""Portfolio UI: display persisted receipts; no clients, stores, queries or training."""
import json
from pathlib import Path

import streamlit as st

from ecalx.presentation.replay import (historical_runs, load_history,
                                       load_public, story_view)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def introduction():
    st.title('ECalX · 可追溯材料研究 Agent')
    st.markdown('**把开放科研需求变成可检查的研究过程。** LLM 负责规划与工具选择，Python 保存参数、来源和校验收据；报告必须解释哪些结论仍然未知。')
    columns = st.columns(3)
    for col, title, body in zip(columns, ['01 · 需求与规划', '02 · 工具与证据', '03 · 报告与边界'],
            ['冻结用户 Goals，未指定项保持空。', 'Function Calling → 参数校验 → 来源与逐候选检查。', '工具成功、摘要验收、科学审核分别判断。']):
        with col.container(border=True):
            st.markdown('**'+title+'**')
            st.caption(body)
    st.caption('Python · Pydantic · Streamlit · SQLite · DeepSeek Function Calling（真实执行属于历史证据；当前页面只读）')
    st.info('只读回放 · 本次 API 请求 0 · 不执行检索、实验或训练 · M5 正式推荐禁用')
    with st.sidebar:
        st.markdown('### 3 分钟阅读路线')
        st.markdown('1. 需求与冻结 Goals\n2. 工具时间线与参数\n3. 候选 → 来源 → 化学收据\n4. unknown 与分层验收')
        st.caption('A = 本机 LIVE_LLM 历史\n\nB = 获准 TEST_ONLY 包\n\n待审预览 = 独立本地入口')


def render(root):
    root = Path(root)
    introduction()
    mode = st.radio('演示证据来源', ['A · 本机真实历史回放', 'B · 干净环境公开演示'], horizontal=True, key='replay_mode')
    try:
        if mode.startswith('A'):
            st.caption('LIVE_LLM 历史证据 · 仅读取本机保存工件 · 未获准公开的原件只供本机审核')
            runs = historical_runs(root)
            if not runs:
                st.info('尚无真实验证记录。本检出不包含私有历史轨迹或科研数据库。')
                empty_guide()
                return
            default = next((i for i, p in enumerate(runs) if p.parent.name == '14f8ba4b8a1e4ebe9bfcbf3ad6ea75ca'), 0)
            path = st.selectbox('选择保存的运行', runs, index=default,
                                format_func=lambda p: p.parent.parent.parent.name+' · '+p.parent.name,
                                key='portfolio_run')
            packet = load_history(root, path)
        else:
            st.caption('TEST_ONLY 合成展示专用 · 不代表 DeepSeek 真实运行、供应商用量或科学成绩')
            packet = load_public(root)
            if packet is None:
                st.info('公开演示包待所有者审核（OWNER_REVIEW）。当前没有获准公开的最小样例。')
                empty_guide()
                return
        render_packet(packet)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        # Never expose an exception containing a private absolute path or artifact text.
        st.warning('保存工件缺失、损坏或审核未通过；本页面不会重新执行任务。')
        st.caption('诊断类别：'+type(error).__name__)
        empty_guide()
        return


def empty_guide():
    st.markdown('**可复现的下一步**：当前空态即可验证无 Key 启动。公开包须按 `docs/clean_demo.md` 完成内容、许可、隐私检查及所有者最终批准；本机历史原件继续留在忽略的 `reports/`。')
    st.markdown('**工程流程**：需求 → 冻结 Goals → Function Calling → 证据检索 → 规则候选 → 独立化学检查 → 报告与分层审核。')
    st.caption('不自动下载数据库，不创建账本，不用模拟成绩补真实成绩。')


def render_packet(packet):
    task, summary = packet['task'], packet['summary']
    view = story_view(packet)
    live = packet['mode'] == 'LIVE_LLM'
    with st.sidebar:
        suffix = '' if live else '-test-only'
        st.markdown('**跳转到证据**')
        st.markdown('\n\n'.join('['+label+'](#'+str(i)+suffix+')' for i, label in
            [(1, '需求与冻结 Goals'), (2, '工具时间线'), (3, '候选与来源'), (4, '科学不确定性'), (5, '报告与分层状态')]))
    st.subheader('运行概览 · '+packet['mode'])
    if live:
        posthoc = packet.get('posthoc_review')
        st.warning('原历史运行状态：'+summary.get('status', 'UNKNOWN')+' · 事后复评（POSTHOC_REVIEW）：'+(posthoc['POSTHOC_VALIDATOR_REVIEW'] if posthoc else 'NOT_AVAILABLE')+' · 独立科学审核：待审')
        columns = st.columns(4)
        for col, label, value in zip(columns, ['历史 API 请求', '历史供应商报告 Token', '历史任务秒数', '历史本地估算 ¥'],
                [summary.get('actual_requests'), summary.get('actual_total_tokens'),
                 round(summary.get('elapsed_seconds', 0), 2), summary.get('estimated_cost_cny')]):
            col.metric(label, str(value) if value is not None else '未知')
        st.caption('仅该运行的历史统计；费用为本地估算，非供应商账单。工具收据耗时不是完整任务墙钟耗时。')
    else:
        rounds = '五轮' if len(packet['responses']) == 5 else str(len(packet['responses']))+'轮'
        compositions = '两个' if len(view['candidates']) == 2 else str(len(view['candidates']))+'个'
        st.warning('TEST_ONLY · '+rounds+'为预写模拟；未运行真实 DeepSeek 或科研工具。chemical_pass 是模拟字段；'+compositions+'组成未经验证，性能 unknown，M5 禁用。')
        if packet.get('local_preview'):
            st.caption('TEST_ONLY 本地待审预览；尚无所有者最终批准，B 模式仍保持待审空态。所有者批准不属于独立科学审核。')
    st.markdown('**读图顺序：** '+str(len(view['timeline']))+' 条工具收据 → '+str(len(view['candidates']))+' 条候选 → 来源关联 → 分层验收。')
    st.subheader('1 · 用户需求与模型研究计划' if live else '1 · TEST_ONLY 合成需求与模拟计划')
    st.text(task.get('original') or '原始需求未保存')
    goals = task.get('frozen_goals', {})
    st.write('实际生效的排除元素：'+('、'.join(goals.get('exclude_elements', [])) or '未指定'))
    st.caption('冻结目标是权威约束；未指定项保持空，不从当前候选家族推断用户偏好。')
    st.write('保存计划：'+' → '.join(task.get('plan', {}).get('steps', [])))
    if packet.get('planning_response'):
        st.caption('DeepSeek PlanningProposal 已匹配原响应 SHA-256 与本任务计划；计划经人审后执行。此处不重新读取 API 账本。')
        with st.expander('真实模型规划响应与来源绑定'):
            st.json({'proposal_source': task.get('proposal_source'), 'response': packet['planning_response']})
    elif live:
        st.caption('规划响应原件缺失或未匹配哈希；这里只展示任务中保存的计划，不能单凭计划字段确认模型规划来源。')
    with st.expander('冻结 Goals、计划与批准记录'):
        st.json({k: task.get(k) for k in ('frozen_goals', 'plan', 'plan_origin', 'goals_authority', 'approvals')})
    st.subheader('2 · 模型自主选择的工具时间线' if live else '2 · TEST_ONLY 模拟工具顺序（未执行）')
    if view['timeline']:
        st.dataframe(view['timeline'] if live else [{('TEST_ONLY 预写工具' if k == '模型选择的工具' else k): v for k, v in row.items()} for row in view['timeline']], hide_index=True, width='stretch')
        calls = task.get('calls', [])
        selected = st.selectbox('查看科研工具调用', range(len(calls)),
                               format_func=lambda i: str(i+1)+' · '+calls[i]['name']+' · '+calls[i]['id'])
        call = calls[selected]
        with st.expander('工具参数、执行状态与校验说明', expanded=True):
            if not live:
                st.caption(call.get('selection_reason', 'TEST_ONLY 模拟选择；没有模型调用'))
                st.caption(call.get('validation_note', 'TEST_ONLY 仅用于显示'))
            left, right = st.columns(2)
            with left:
                st.markdown('**输入参数**')
                st.json(call['arguments'])
            with right:
                st.markdown('**保存的工具收据**' if live else '**TEST_ONLY 模拟工具收据**')
                st.caption('保存状态：'+call['output']['status']+('' if live else '（TEST_ONLY 模拟，未执行）'))
                st.json(call['output'], expanded=False)
    else:
        st.info('该运行没有保存已执行的工具调用。')
    st.caption('时间线来自原始 calls 顺序；页面不重放调用，不重新计算化学规则。' if live else 'TEST_ONLY 秒数是脚本叙事位置，不是耗时测量；所有 OK、chemical_pass 均为模拟值。')
    if not live and task and packet.get('responses'):
        st.subheader('TEST_ONLY 预写模拟轮次（未执行）')
        st.caption('TEST_ONLY_AUTHORED_SIMULATED：以下仅为预写科研因果叙事，每轮未执行真实LLM、网络请求或工具执行，不能证明多轮自主决策。')
        st.dataframe([{'模拟轮次': r.get('simulated_round', i),
                       '响应ID': r.get('response_id'),
                       '预写call ID': ' / '.join(c['id'] for c in r.get('calls', [])),
                       '叙事依赖call ID': ' / '.join(r.get('simulated_depends_on_call_ids', [])),
                       '模拟说明': r.get('visible_text')}
                      for i, r in enumerate(packet['responses'], 1)], hide_index=True, width='stretch')
    st.subheader('3 · 文献来源与候选的证据链' if live else '3 · TEST_ONLY 合成来源与候选占位')
    if view['sources']:
        st.dataframe(view['sources'] if live else [{('TEST_ONLY 模拟来源组成' if k == '历史组成' else k): v for k, v in row.items()} for row in view['sources']], hide_index=True, width='stretch',
                     column_config={'DOI链接': st.column_config.LinkColumn('DOI链接')})
        st.caption('这些是检索工具返回的本地文献汇编记录，不能称为本轮在线全文检索。DOI链接仅在主动点击时打开；历史 ΔT 不是新候选的实测值。电场端点/路径缺失保持未知，不跨协议排名。' if live else 'TEST_ONLY 来源是原创占位记录，不对应任何论文或真实数据库行。DOI、温度、电场、ΔT 均为空，空值不是 0。')
    if view['candidates']:
        st.dataframe(view['candidates'] if live else [{({'独立chemical_pass': 'TEST_ONLY 模拟化学校验收据（未执行）', '组成': 'TEST_ONLY 模拟候选组成'}.get(k, k)): v for k, v in row.items()} for row in view['candidates']], hide_index=True, width='stretch')
        selected_candidate = st.selectbox('查看候选的来源与化学检查', range(len(view['details'])),
            format_func=lambda i: view['details'][i]['row']['组成']+' · '+view['details'][i]['candidate']['candidate_id'])
        for detail in [view['details'][selected_candidate]]:
            row, candidate, receipt = detail['row'], detail['candidate'], detail['receipt']
            with st.expander(row['组成']+' · '+row['性能']+' · 化学收据 '+row['化学收据'], expanded=True):
                st.markdown('**关联链**：'+candidate['candidate_id']+' → '+str(row['来源种子'])+' → '+row['化学收据'])
                st.write('身份：'+str(row['身份'])+'；种子：'+str(row['来源种子']))
                st.write('关联 DOI：'+row['种子DOI'])
                if detail['missing_source_ids']:
                    st.warning('部分种子来源未保存，关联不完整。')
                checks = candidate.get('chemical_checks', {})
                st.dataframe([{'生成器基础规则': k, '保存结果': str(v)} for k, v in checks.items()], hide_index=True, width='stretch')
                st.caption('上表来自生成收据；下方是模型另行调用 validate_chemical_constraints 得到的独立收据。规则通过仅在声明价态与家族假设下成立。' if live else 'TEST_ONLY 生成规则和逐候选化学收据均为人工编写的模拟值，未执行化学计算，也不是独立专家审核。')
                if receipt:
                    st.write(('独立 chemical_pass：' if live else 'TEST_ONLY 模拟 chemical_pass（未执行）：')+str(receipt['output']['data'].get('chemical_pass')))
                    st.json(receipt, expanded=False)
                else:
                    st.warning('独立化学收据缺失或存在歧义，不能标记独立通过。')
                st.json({'generation_sources': candidate.get('generation_sources'),
                         'oxidation_states_assumed': candidate.get('oxidation_states_assumed'),
                         'site_fractions': candidate.get('site_fractions'),
                         'verification_gaps': candidate.get('verification_gaps')}, expanded=False)
    else:
        st.info('该运行没有生成候选，不能补造候选展示。')
    st.subheader('4 · 科学问题仍待验证' if live else '4 · TEST_ONLY 不构成科学证据')
    st.warning('generated_unverified / performance unknown。基础价态、计量、占位和电荷规则通过，不证明晶体结构、相稳定性、可合成性或电卡性能。没有可靠定量排名、全球新颖性证明或已验证高性能材料发现。' if live else 'TEST_ONLY：chemical_pass=true和OK只是预写模拟值。形式化学计量合理，不证明真实结构或科学性能。Sr/Ca/Ti/O配比不声明全球新颖性或材料首次发现。所有候选性能仍为unknown。没有真实DeepSeek请求、科研实验、训练或正式推荐。结构未验证，M5禁用。')
    st.caption('实验与科学结论只以该运行原始收据为准；规则通过与事后复评不替代独立科学审核。M5正式推荐禁用。' if live else 'TEST_ONLY 未执行任何工具、训练、虚拟或真实实验；科学审核待完成，M5禁用。')
    st.subheader('5 · 摘要、原验收、事后复评与科学人审' if live else '5 · TEST_ONLY 模拟摘要与展示状态')
    guard = packet.get('original_guard')
    original_gate = ('PASSED' if guard['passed'] else 'FAILED') if guard is not None else 'NOT_SAVED'
    review = packet.get('posthoc_review')
    status_rows = [
        {'判断层': '科研工具执行', '状态': view['tool_status']},
        {'判断层': 'LLM摘要返回', '状态': view['summary_response_status']},
        {'判断层': '原自动摘要门禁', '状态': original_gate},
        {'判断层': '原在线端到端验收' if live else 'TEST_ONLY 合成状态', '状态': summary.get('status', 'UNKNOWN')},
        {'判断层': 'POSTHOC_REVIEW · 原响应离线复评', '状态': review['POSTHOC_VALIDATOR_REVIEW'] if review else 'NOT_AVAILABLE'},
        {'判断层': '独立人工科学审核', '状态': view['human_scientific_review']},
    ]
    if not live:
        status_rows = [{**r, '判断层': 'TEST_ONLY · '+r['判断层']} for r in status_rows]
        for row in status_rows[:2]:
            row['状态'] = 'TEST_ONLY_SIMULATED_NOT_EXECUTED' if '科研工具' in row['判断层'] else 'TEST_ONLY_AUTHORED_NO_LLM_REQUEST'
    st.dataframe(status_rows, hide_index=True, width='stretch')
    if live:
        st.info('ORIGINAL_LIVE_STATUS: '+summary.get('status', 'UNKNOWN')+
                ' · POSTHOC_VALIDATOR_REVIEW: '+(review['POSTHOC_VALIDATOR_REVIEW'] if review else 'NOT_AVAILABLE')+
                ' · LIVE_END_TO_END_ACCEPTANCE: '+summary.get('status', 'UNKNOWN'))
    if view['final_response']:
        st.text(view['final_response']['visible_text'])
        st.caption('保存的最终模型正文，未经改写；完整 stop 不等于门禁通过或科学验证完成。' if live else 'TEST_ONLY 人工编写的模拟摘要，未调用模型；不属于真实LLM证据。')
    else:
        st.info('没有保存最终模型摘要。工具结果与原失败继续保留。')
    with st.expander('原自动门禁与独立事后复评原件'):
        st.json({'original_guard': guard, 'posthoc_review': review})
    if not live:
        reports = [c['output']['data']['report'] for c in task.get('calls', []) if c['name'] == 'finish_research' and 'report' in c['output']['data']]
        if reports:
            with st.expander('TEST_ONLY 结构化报告 · 来源指针与科学缺口', expanded=True):
                st.caption('报告 next_steps 中的独立内容审核要求属于保存的旧治理文字；当前 TEST_ONLY 发布采用所有者审核。独立科学审核仍未完成，不作为合成演示公开的必要条件。')
                st.json(reports[0])
    st.subheader('6 · 原始收据与下载')
    st.caption('本机下载保留原件；未经许可和脱敏审核，不代表这些文件获准公开。' if live else 'TEST_ONLY 本地预览下载不代表公开授权；只有所有者最终批准的文件范围可以公开。')
    if live:
        directory = packet['directory']
        for filename, label in [('structured_report.json', '下载完整结构化科研报告'),
                                ('complete_sanitized_trace.json', '下载完整脱敏轨迹'),
                                ('summary_guard.json', '下载原自动门禁收据')]:
            path = directory/filename
            if path.is_file() and not path.is_symlink():
                st.download_button(label, path.read_bytes(), filename, 'application/json')
        with st.expander('模型请求响应详情（历史用量与完成原因）'):
            st.json(packet['responses'])
        if review:
            st.download_button('下载独立事后复评证据', json_text(review), 'posthoc_review.json', 'application/json')
        st.download_button('下载运行记录与原始工具收据', json_text({'summary': summary, 'task': task}), 'saved_run.json', 'application/json')
    else:
        label = '下载 TEST_ONLY 待审候选（未批准）' if packet.get('local_preview') else '下载已审核 TEST_ONLY 展示包'
        st.download_button(label, (packet['directory']/'payload.json').read_bytes(), 'TEST_ONLY_payload.json', 'application/json')
        with st.expander('TEST_ONLY 待审记录（空批准字段）' if packet.get('local_preview') else '公开包所有者审核记录（非独立科学审核）'):
            st.json(packet['public_review'])
