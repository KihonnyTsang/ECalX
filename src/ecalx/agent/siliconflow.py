"""SiliconFlow Chat Completions transport; only actual message.tool_calls execute.

No SDK, raw HTTP logging, text/JSON tool emulation, endpoint fallback or key fields
in the client object. Defaults disable thinking to bound generated token costs.
"""
import json
import hashlib
import os
import re
from uuid import uuid4
import time
import urllib.error
import urllib.request

from pydantic import Field
from ecalx.experiments.contracts import Contract
from .config import siliconflow_settings
from .llm import SYSTEM, ProviderError, Reply, redact
from .api_policy import enforce_request_policy, complete_request_policy, request_deadline


class LLMBudget(Contract):
    llm_requests: int = Field(default=12, ge=1, le=48)
    tool_calls: int = Field(default=32, ge=1, le=64)
    request_tokens: int = Field(default=2048, ge=64, le=4096)
    total_tokens: int = Field(default=131072, ge=1024, le=1000000)
    request_seconds: float = Field(default=25, ge=1, le=180)
    seconds: float = Field(default=300, ge=1, le=600)
    transport_retries: int = Field(default=0, ge=0, le=2)


class TokenPrices(Contract):
    """User-confirmed CNY / million tokens; never silently substitute stale pricing."""
    input_per_million: float = Field(ge=0, le=10000)
    output_per_million: float = Field(ge=0, le=10000)
    cached_input_per_million: float | None = Field(default=None, ge=0, le=10000)


def usage_stats(raw, prices=None):
    def count(name):
        value = raw.get(name)
        return value if type(value) is int and value >= 0 else None
    prompt, completion = count('prompt_tokens'), count('completion_tokens')
    total = count('total_tokens')
    # Preserve the provider's total, including absence/inconsistency. The shared
    # policy retains reservations or charges max(total, input+output) and stops.
    details = raw.get('prompt_tokens_details')
    details = details if isinstance(details, dict) else {}
    cached = count('prompt_cache_hit_tokens')
    if cached is None:
        value = details.get('cached_tokens')
        cached = value if type(value) is int and value >= 0 else 0
    cached = min(cached, prompt or 0)
    cost = None
    if prices is not None and prompt is not None and completion is not None:
        cache_rate = prices.cached_input_per_million
        cache_rate = prices.input_per_million if cache_rate is None else cache_rate
        cost = ((prompt - cached) * prices.input_per_million + cached * cache_rate
                + completion * prices.output_per_million) / 1_000_000
    return {'input_tokens': prompt, 'output_tokens': completion, 'total_tokens': total,
            'cached_input_tokens': cached, 'estimated_cost_cny': cost,
            'cost_status': 'USER_RATE_ESTIMATE' if cost is not None else 'UNAVAILABLE_USAGE_OR_PRICE'}


def response_metadata(body):
    """Allowlisted diagnostics only: never retain raw JSON, reasoning or headers."""
    choices = body.get('choices') if isinstance(body, dict) else None
    result = []
    for choice in choices if isinstance(choices, list) else []:
        choice = choice if isinstance(choice, dict) else {}
        reason = choice.get('finish_reason')
        message = choice.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        visible = redact(content or '') if content is None or isinstance(content, str) else None
        result.append({'content_field_valid': content is None or isinstance(content, str),
            'finish_reason': reason if reason in ('stop', 'tool_calls', 'length',
            'content_filter', None) else 'UNKNOWN_PROTOCOL_VALUE',
            'visible_text_characters': len(visible) if visible is not None else None,
            'visible_text_sha256': hashlib.sha256(visible.encode()).hexdigest()
                if visible is not None else None})
    return {'choices': result, 'choices_field_valid': isinstance(choices, list),
            'visible_text_hash_scope': 'REDACTED_VISIBLE_CONTENT_ONLY'}


def chat_messages(messages, system=SYSTEM):
    """Convert existing Responses-shaped history without elevating untrusted content."""
    converted = [{'role': 'system', 'content': system}]
    for item in messages:
        kind = item.get('type')
        if kind == 'function_call':
            converted.append({'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': item['call_id'], 'type': 'function', 'function': {
                    'name': item['name'], 'arguments': item['arguments']}}]})
        elif kind == 'function_call_output':
            converted.append({'role': 'tool', 'tool_call_id': item['call_id'], 'content': item['output']})
        elif kind == 'reasoning':
            continue  # OpenAI encrypted reasoning is not Chat Completions content.
        elif item.get('role') in {'user', 'assistant', 'tool'}:
            converted.append({k: v for k, v in item.items()
                              if k in {'role', 'content', 'tool_calls', 'tool_call_id', 'reasoning_content'}})
        else:
            raise ProviderError('上下文消息类型非法', code='INVALID_HISTORY')
    pending, seen = set(), set()
    for message in converted:
        if message['role'] == 'tool':
            cid = message.get('tool_call_id')
            if cid not in pending:
                raise ProviderError('工具结果ID不匹配或重复', code='INVALID_HISTORY')
            pending.remove(cid)
        else:
            if pending:
                raise ProviderError('工具结果未齐，禁止插入其他消息', code='INVALID_HISTORY')
            for call in message.get('tool_calls', []):
                cid = call.get('id')
                if not isinstance(cid, str) or not cid or cid in seen:
                    raise ProviderError('历史调用ID非法或重复', code='INVALID_HISTORY')
                pending.add(cid)
                seen.add(cid)
    if pending:
        raise ProviderError('工具结果缺失，禁止发送不完整历史', code='INVALID_HISTORY')
    return redact(converted)


def completion_payload(model, messages, tools, max_tokens, tool_choice, *, system=SYSTEM):
    """Pure builder shared by transport and offline request inspection."""
    payload = {'model': model, 'messages': chat_messages(messages, system), 'stream': False,
               'max_tokens': max_tokens, 'enable_thinking': False}
    if tools:
        payload.update(tools=[{'type': 'function', 'function': {
            'name': t['name'], 'description': t['description'], 'parameters': t['parameters']}}
            for t in tools], tool_choice=tool_choice)
    elif tool_choice == 'required':
        raise ProviderError('结构化工具列表为空', code='INVALID_TOOLS')
    else:
        payload['tool_choice'] = 'none'
    return payload


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError('供应商重定向被拒绝', code='REDIRECT_REJECTED')


class SiliconFlowChatCompletionsClient:
    mode = 'LIVE_LLM'
    provider = 'siliconflow'
    system_prompt = SYSTEM
    max_body_bytes = 200000

    def request_body(self, messages, tools, tool_choice='required'):
        """One encoder for HTTP and offline byte auditing; no credentials in body."""
        payload = completion_payload(self.model, messages, tools, self.budget.request_tokens,
                                     tool_choice, system=self.system_prompt)
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')

    def __init__(self, budget=None, prices=None, *, confirmed_paid=False, api_policy=None):
        if confirmed_paid is not True:
            raise ProviderError('需单独明确付费授权', code='PAID_CONFIRMATION_REQUIRED')
        from .api_policy import ProjectAPIPolicy
        if not isinstance(api_policy, ProjectAPIPolicy):
            raise ProviderError('需宿主注入独立策略及账本', code='EGRESS_PROFILE_REQUIRED')
        self.api_policy = api_policy
        self.model, self.base_url = siliconflow_settings()
        self.budget = LLMBudget.model_validate(budget or {})
        self.prices = TokenPrices.model_validate(prices) if prices is not None else None

    def respond(self, messages, tools):
        return self.respond_budgeted(messages, tools, remaining_requests=self.budget.llm_requests,
                                     remaining_tokens=self.budget.total_tokens,
                                     timeout=self.budget.request_seconds)

    def respond_budgeted(self, messages, tools, *, remaining_requests, remaining_tokens,
                         timeout, checkpoint=None, tool_choice='required', on_attempt=None):
        """Retries consume attempts and conservative token reservations, including failures.

        Default zero retries. Only 429/503 retry; ambiguous network failures never
        retry automatically. Callers must persist metrics on both success/failure.
        """
        data = self.request_body(messages, tools, tool_choice)
        payload = json.loads(data)
        if len(data) > self.max_body_bytes:
            raise ProviderError('上下文超过资源限制', code='CONTEXT_LIMIT')
        # No local provider tokenizer: conservative UTF-8 byte reservation plus framing.
        # Thinking is disabled; observed provider usage supersedes successful reservation.
        reserve = len(data) + 1024 + self.budget.request_tokens
        metrics = {'requests': 0, 'accounted_tokens': 0, 'elapsed_seconds': 0.,
                   'attempts': [], 'estimated_cost_cny': None}
        start = time.monotonic()
        reservation = None
        allowed = min(remaining_requests, 1 + self.budget.transport_retries)
        try:
            for _ in range(allowed):
                if checkpoint:
                    checkpoint()
                if reserve > remaining_tokens - metrics['accounted_tokens']:
                    raise ProviderError('任务Token预算不足以预留本次请求', code='TOKEN_LIMIT')
                left = min(timeout, self.budget.request_seconds) - (time.monotonic() - start)
                if left <= 0:
                    raise ProviderError('请求总时限耗尽', code='TIMEOUT')
                reservation, left = enforce_request_policy(self, data, left)
                local_trace_id = reservation or uuid4().hex
                metrics['requests'] += 1
                metrics['accounted_tokens'] += reserve
                attempt = {'local_trace_id':local_trace_id, 'configured_model':self.model,
                           'request_index': metrics['requests'], 'reserved_tokens': reserve,
                           'status': 'STARTED', 'usage': {}, 'timeout_seconds': left,
                           'phase': 'CONNECT_OR_RESPONSE_HEADERS', 'response_received': False,
                           'http_status': None, 'response_complete': False,
                           'headers_received': False,
                           'connect_and_headers_seconds': None, 'read_seconds': None,
                           'transport': {'library': 'urllib', 'opener_scope': 'PER_ATTEMPT',
                               'connection_pool': False,
                               'proxy_schemes_present': sorted(k for k in urllib.request.getproxies()
                                   if k in {'http', 'https', 'all'}),
                               'proxy_route_observed': False},
                           'usage_status': 'UNKNOWN_RESERVED', 'payload_bytes': len(data),
                           'message_roles': [m['role'] for m in payload['messages']],
                           'tool_definitions': len(tools),
                           'max_tokens': payload['max_tokens'],
                           'enable_thinking': payload['enable_thinking'],
                           'tool_choice': payload.get('tool_choice')}
                metrics['attempts'].append(attempt)
                if on_attempt:
                    on_attempt(metrics)  # Durable reservation BEFORE a potentially paid send.
                if checkpoint:
                    checkpoint()
                request_start = time.monotonic()
                request = urllib.request.Request(self.base_url + '/chat/completions', data=data,
                    headers={'Authorization': 'Bearer ' + os.environ['SILICONFLOW_API_KEY'],
                             'Content-Type': 'application/json', 'X-Trace-Id':local_trace_id})
                try:
                    # Destination revalidated every request, including after environment changes.
                    if siliconflow_settings() != (self.model, self.base_url):
                        raise ProviderError('提供方配置在任务中发生变化', code='INVALID_CONFIG')
                    with request_deadline(reservation, left), urllib.request.build_opener(NoRedirect()).open(request, timeout=left) as response:
                        attempt.update(headers_received=True,
                            http_status=getattr(response, 'status', None),
                            connect_and_headers_seconds=time.monotonic()-request_start)
                        content_length = getattr(response, 'headers', {}).get('Content-Length')
                        expected_bytes = int(content_length) if (isinstance(content_length, str)
                            and content_length.isdecimal()) else None
                        trace = getattr(response, 'headers', {}).get('x-siliconcloud-trace-id')
                        if (isinstance(trace, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', trace)
                                and redact(trace) == trace):
                            attempt['provider_trace_id'] = trace
                        elif trace is not None:
                            attempt['provider_trace_header_status'] = 'INVALID_NOT_RECORDED'
                        attempt['phase'] = 'READ_RESPONSE'
                        read_start = time.monotonic()
                        try:
                            raw = response.read(1_000_001)
                        finally:
                            attempt['read_seconds'] = time.monotonic()-read_start
                    attempt.update(response_received=True, phase='PARSE_RESPONSE')
                    attempt.update(response_bytes=len(raw), expected_response_bytes=expected_bytes,
                        response_complete=len(raw)<=1_000_000 and
                            (expected_bytes is None or expected_bytes==len(raw)))
                    if len(raw) > 1_000_000:
                        raise ProviderError('供应商响应超过限制', code='RESPONSE_LIMIT')
                    if not attempt['response_complete']:
                        attempt['error_category'] = 'HTTP_INCOMPLETE'
                        raise ProviderError('HTTP响应未完整到达', code='INVALID_RESPONSE')
                    body = json.loads(raw)
                    attempt['response_metadata'] = response_metadata(body)
                    if not isinstance(body, dict):
                        raise ValueError
                    attempt['actual_model'] = body.get('model') if body.get('model') == self.model else 'MISMATCH'
                    usage = body.get('usage') or {}
                    usage = usage if isinstance(usage, dict) else {}
                    stats = usage_stats(usage, self.prices)
                    attempt.update(status='RECEIVED', usage=stats)
                    if stats['total_tokens'] is not None:
                        metrics['accounted_tokens'] += stats['total_tokens'] - reserve
                        attempt['usage_status'] = 'PROVIDER_REPORTED'
                    costs = [a.get('usage', {}).get('estimated_cost_cny') for a in metrics['attempts']]
                    metrics['estimated_cost_cny'] = sum(costs) if all(c is not None for c in costs) else None
                    if checkpoint:
                        checkpoint()
                    if time.monotonic() - start > min(timeout, self.budget.request_seconds):
                        raise ProviderError('请求超时；不执行迟到的工具调用', code='TIMEOUT')
                    if metrics['accounted_tokens'] > remaining_tokens:
                        raise ProviderError('提供方实际Token超过任务限额', code='TOKEN_LIMIT')
                    attempt['phase'] = 'VALIDATE_MODEL_CONTENT'
                    try:
                        reply = self._decode(body, tools, tool_choice)
                    except ProviderError as error:
                        reason = body.get('choices', [{}])[0].get('finish_reason') if (
                            isinstance(body.get('choices'), list) and len(body['choices'])==1
                            and isinstance(body['choices'][0], dict)) else None
                        attempt['error_category'] = ('RESPONSE_LENGTH' if reason=='length' else
                            'CONTENT_FILTER' if reason=='content_filter' else
                            'EMPTY_TEXT' if error.code=='INCOMPLETE_RESPONSE' else
                            'PROTOCOL_ERROR' if error.code in {'INVALID_RESPONSE','INVALID_TOOL_CALL'} else
                            'MODEL_CONTENT_ERROR')
                        raise
                    reply.usage = stats
                    reply.metrics = metrics
                    return reply
                except urllib.error.HTTPError as error:
                    status = error.code
                    error.close()  # Do not read/persist provider error body or headers.
                    retryable = status in {429, 503}
                    attempt.update(status='HTTP_ERROR', http_status=status)
                    if retryable and metrics['requests'] < allowed:
                        # Bounded backoff shares deadline; no Retry-After header leakage.
                        delay = min(0.25 * 2 ** (metrics['requests'] - 1), max(0, left / 4))
                        time.sleep(delay)
                        continue
                    raise ProviderError('真实提供方HTTP请求失败', code='HTTP_ERROR',
                                        http_status=status, retryable=retryable) from None
                except (TimeoutError, urllib.error.URLError, OSError) as error:
                    reason = error.reason if isinstance(error, urllib.error.URLError) else error
                    is_timeout = isinstance(reason, TimeoutError)
                    code = ('READ_TIMEOUT' if attempt['phase'] == 'READ_RESPONSE' else 'REQUEST_TIMEOUT') if is_timeout else 'CONNECTION_ERROR'
                    attempt['status'] = code
                    raise ProviderError('提供方读取超时；用量未知，未自动重发' if code == 'READ_TIMEOUT' else
                                        '提供方请求超时；用量未知，未自动重发' if is_timeout else
                                        '提供方连接或传输失败；用量未知，未自动重发', code=code) from None
                finally:
                    attempt['elapsed_seconds'] = time.monotonic() - request_start
                    if not attempt['headers_received']:
                        attempt['connect_and_headers_seconds'] = attempt['elapsed_seconds']
            raise ProviderError('LLM请求次数上限已达到', code='REQUEST_LIMIT')
        except ProviderError as error:
            if metrics['attempts']:
                attempt = metrics['attempts'][-1]
                attempt['error_code'] = error.code
                attempt.setdefault('error_category', ('MODEL_CONTENT_ERROR' if error.code in {
                    'INCOMPLETE_RESPONSE', 'NO_TOOL_CALLS', 'UNEXPECTED_TOOL_CALLS'} else
                    'PROTOCOL_ERROR' if error.code in {'INVALID_RESPONSE', 'INVALID_TOOL_CALL'} else error.code))
            error.metrics = metrics
            raise
        except Exception:
            if metrics['attempts']:
                metrics['attempts'][-1].update(status='PROTOCOL_ERROR', error_code='INVALID_RESPONSE',
                                               error_category='PROTOCOL_ERROR')
            raise ProviderError('供应商响应协议非法', code='INVALID_RESPONSE', metrics=metrics) from None
        finally:
            metrics['elapsed_seconds'] = time.monotonic() - start
            if reservation is not None:
                try:
                    complete_request_policy(self, reservation, metrics['attempts'][-1])
                except ProviderError as error:
                    error.metrics = metrics
                    raise

    def _decode(self, body, tools, tool_choice):
        choices = body.get('choices')
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderError('需要单个完整模型响应', code='INVALID_RESPONSE')
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError('响应choice字段非法', code='INVALID_RESPONSE')
        if choice.get('finish_reason') in {'length', 'content_filter'}:
            raise ProviderError('模型响应截断/过滤；不执行部分调用', code='INCOMPLETE_RESPONSE')
        if choice.get('finish_reason') not in {'stop', 'tool_calls'}:
            raise ProviderError('响应finish_reason字段非法', code='INVALID_RESPONSE')
        message = choice.get('message', {})
        if not isinstance(message, dict) or message.get('role') != 'assistant' or body.get('model') != self.model:
            raise ProviderError('模型ID或消息角色不匹配', code='INVALID_RESPONSE')
        content = message.get('content')
        reasoning = message.get('reasoning_content')
        if content is not None and not isinstance(content, str):
            raise ProviderError('模型可见文本字段非法', code='INVALID_RESPONSE')
        if reasoning is not None and not isinstance(reasoning, str):
            raise ProviderError('模型推理字段非法', code='INVALID_RESPONSE')
        calls = message.get('tool_calls') or []
        if not isinstance(calls, list) or len(calls) > self.budget.tool_calls:
            raise ProviderError('结构化工具调用数量非法', code='TOOL_LIMIT')
        if not calls and (tool_choice == 'required' or choice['finish_reason'] != 'stop'):
            raise ProviderError('未收到真实tool_calls；文字/JSON/推理不是工具执行证据', code='NO_TOOL_CALLS')
        if calls and tool_choice == 'none':
            raise ProviderError('禁止调用工具的最终回合返回了tool_calls', code='UNEXPECTED_TOOL_CALLS')
        if not calls and not (content or '').strip():
            raise ProviderError('最终正文为空', code='INCOMPLETE_RESPONSE')
        decoded, ids = [], set()
        for call in calls:
            cid = call.get('id')
            function = call.get('function', {})
            arguments = function.get('arguments')
            if (call.get('type') != 'function' or not isinstance(cid, str) or not 0 < len(cid) <= 100
                    or cid in ids or not isinstance(function.get('name'), str)
                    or not isinstance(arguments, str) or len(arguments.encode()) > 16000):
                raise ProviderError('结构化工具调用格式非法', code='INVALID_TOOL_CALL')
            # Unknown tools are rejected by runtime permissions; no model-supplied code.
            parsed = json.loads(arguments, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(parsed, dict):
                raise ProviderError('工具参数必须是JSON对象', code='INVALID_TOOL_CALL')
            ids.add(cid)
            decoded.append({'id': cid, 'name': function['name'], 'arguments': parsed})
        assistant = {'role': 'assistant', 'content': content}
        if calls:
            assistant['tool_calls'] = calls
        if reasoning is not None:
            # Opaque protocol history only, hidden from UI/audit/report. Never parsed.
            assistant['reasoning_content'] = reasoning
        return Reply(decoded, model=self.model, output_items=[redact(assistant)],
                     visible_text=redact(content or ''))
