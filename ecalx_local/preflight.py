"""Read-only readiness, no key loading, task creation, network or ledger writes."""
from datetime import datetime
from zoneinfo import ZoneInfo
from . import SCIENCE_ROOT, PERSONAL
from .engine import CORE, BUDGET, LiveClient, online_quote
from ecalx.agent.api_policy import settings, budget_status
from ecalx.agent.config import MODEL, BASE_URL
from ecalx.agent.contracts import Goals, definitions
from .engine import ProjectedPolicy
from ecalx.agent.tools import ReadOnlyEvidence


def readonly_preflight(goals):
    goals = Goals(**goals).model_dump()
    if not PERSONAL:
        from .configuration import SCOPE
        try:
            quote=online_quote()
            ready=True
            checks=['已配置 SiliconFlow '+quote['model']+'；尚未验证供应商可达性、凭据或 Function Calling。',
                    '每次请求前检查人工确认、当前配置、请求/Token/每日估算预算。',SCOPE]
        except Exception:
            ready=False;quote=None
            checks=['尚未配置有效在线 Key、模型或预算；离线科研工具可直接使用。',SCOPE]
        return {'goals':goals,'checks':checks,'quote':quote,
                'status':'CONFIGURED_NOT_VERIFIED' if ready else 'OFFLINE_READY',
                'notice':'仅本地配置预检；没有联网，没有验证真实在线模型。',
                'online_acceptance':'NOT_RUN','network_requests':0}
    checks = [f'客户端已接入：{LiveClient.__name__}；模型 {MODEL}；供应商 {BASE_URL}。',
        'Goals：已解析为本次条件；仅在用户确认后由宿主冻结，模型不能修改；本预检不创建任务或授权。',
        '报告：实际工具收据 → 来源/Pointer核验 → 逐候选独立化学检查 → finish；中文下载报告由收据生成。']
    registered = {t['name'] for t in definitions(names=CORE | {'finish_research'})}
    ready = registered == CORE | {'finish_research'}
    checks += ['模型工具注册：' + '、'.join(sorted(registered)) + '；预测、训练、虚拟实验不对在线开放。']
    quote, budget, scope = None, None, None
    try:
        grant, _ = settings(SCIENCE_ROOT)
        quote = online_quote()
        budget = budget_status(SCIENCE_ROOT)
        policy = ProjectedPolicy(SCIENCE_ROOT, ReadOnlyEvidence(SCIENCE_ROOT/'data/processed/evidence.sqlite3'))
        scope = grant['egress_profile']
        if (grant['model'] != MODEL or grant['provider'] != 'siliconflow'
                or grant['endpoint'] != BASE_URL+'/chat/completions'
                or scope != 'A_VERIFIED_MINIMAL_FACTS_MAX4_UNVERIFIED_CANDIDATES'):
            raise ValueError('grant mismatch')
        checks += [f"共享策略可读，未停止；今日已记 {budget['daily_requests_used']} 请求 / {budget['daily_tokens_accounted']:,} Token / ¥{budget['daily_estimated_cny_charge']:.6f} 本地核算；未知用量 {budget['unknown_usage_requests']} 笔沿用原策略。",
            f"预算：预计7–10请求，最多{BUDGET.llm_requests}请求 / {BUDGET.tool_calls}工具 / {BUDGET.total_tokens:,}累计Token / 每请求输出{BUDGET.request_tokens}Token；单请求{BUDGET.request_seconds:g}秒、任务{BUDGET.seconds:g}秒、0重试。",
            f"本地峰时单价：输入¥{quote['prices']['input_per_million']}/百万、输出¥{quote['prices']['output_per_million']}/百万；保守估算¥{quote['max_estimated_cny']:.6f}，有效至{quote['rate_valid_through']}。",
            f'来源出站校验：{len(policy.records)}条A来源的工作簿/许可SHA及记录来源已核对；协议PDF/覆盖层只留本地。',
            '授权：原项目范围已读取并核对配置；本次在线请求未获授权，必须等待用户明确授权并在现有界面确认预算/计划。']
        ready = ready and (budget['daily_requests_used'] + BUDGET.llm_requests <= budget['daily_requests_limit']) and (
            budget['daily_tokens_accounted'] + BUDGET.total_tokens <= budget['daily_tokens_limit']) and (
            budget['daily_estimated_cny_charge'] + quote['max_estimated_cny'] <= budget['daily_estimated_cny_limit'])
    except Exception:
        ready = False
        checks += ['共享策略、单价有效期或来源SHA预检未通过；需按现有错误处理流程解决后再开始。未读取凭据，未发起请求。']
    notice = ('本地准备就绪，等待本次在线授权。' if ready else '当前本地准备检查未通过。') + '离线契约测试不等于在线验收；凭据、账户余额、供应商可达性/响应尚未验证。计划确认等待计入180秒任务时限。'
    return {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'status': 'LOCAL_READY_AWAITING_AUTHORIZATION' if ready else 'LOCAL_CHECK_FAILED',
        'online_acceptance': 'NOT_RUN', 'network_requests': 0,
        'goals': goals, 'checks': checks, 'quote': quote, 'shared_budget': budget,
        'authorization_scope': scope, 'notice': notice}
