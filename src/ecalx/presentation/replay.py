"""Saved artifacts only. Public TEST_ONLY bundles require actual owner publication approval."""
import hashlib
import json
import re
from pathlib import Path

from ecalx.agent.demo_view import saved_run_view, bound_posthoc_review
from .test_only import validate_packet
from .owner_review import approved_review


class ReplayError(ValueError):
    pass


def read_json(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 8_000_000:
        raise ReplayError('工件链接或大小不符合只读演示要求')
    return json.loads(path.read_text(encoding='utf-8'))


def historical_runs(root):
    return sorted((p for stage in ('m4e44', 'm4e46', 'm4e48', 'm4e49', 'm4e410', 'm4e411')
                   for p in (Path(root)/'reports'/stage/'live').glob('*/summary.json')),
                  key=lambda p: (p.parent.parent.parent.name, p.parent.name), reverse=True)


def load_history(root, path):
    root, path = Path(root).resolve(), Path(path).absolute()
    if path not in historical_runs(root):
        raise ReplayError('只能选择本机已登记历史目录')
    directory = path.parent
    if directory.is_symlink() or not directory.resolve().is_relative_to(root/'reports'):
        raise ReplayError('历史目录越界')
    summary, task = read_json(path), read_json(directory/'task.json')
    if summary.get('client_mode') != 'LIVE_LLM' or task.get('client_mode') != 'LIVE_LLM':
        raise ReplayError('缺少 LIVE_LLM 证据标记，不展示为真实运行')
    if summary.get('task_id') != task.get('id'):
        raise ReplayError('任务与运行记录关联不一致')
    responses = [read_json(p) for p in sorted(directory.glob('response_*.json'))]
    packet = {'summary': summary, 'task': task, 'responses': responses,
              'original_guard': read_json(directory/'summary_guard.json')
                  if (directory/'summary_guard.json').is_file() else None,
              'posthoc_review': bound_posthoc_review(root, directory),
              'mode': 'LIVE_LLM', 'directory': directory, 'planning_response': None}
    source = task.get('proposal_source') or {}
    run_id = source.get('run_id', '')
    # This is a hash-bound historical response, not a new ledger or planning request.
    if re.fullmatch(r'[0-9a-f]{32}', run_id):
        plan_file = root/'reports/m4e44/live'/run_id/'response_01.json'
        if plan_file.is_file() and not plan_file.is_symlink():
            raw = plan_file.read_bytes()
            if hashlib.sha256(raw).hexdigest() == source.get('response_sha256'):
                response = read_json(plan_file)
                proposals = [c.get('arguments') for c in response.get('calls', [])
                             if c.get('name') == 'submit_research_plan']
                plan = task.get('plan') or {}
                if proposals == [{k: plan.get(k) for k in ('steps', 'clarification')}]:
                    packet['planning_response'] = response
    return packet


PRIVATE_PATTERN = re.compile(
    r'/Users/|/home/|[A-Za-z]:\\\\Users\\|-----BEGIN [A-Z ]*PRIVATE KEY|'
    r'(?:X-Amz-[A-Za-z-]+|X-Goog-[A-Za-z-]+|AWSAccessKeyId)=|'
    r'https?://[^\s"<>]*[?&](?:Signature|sig|access_token|token)=|'
    r'\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|'
    r'"(?:api_key|access_token|refresh_token|id_token|ledger_entries|authorization)"\s*:', re.I)


def load_public(root):
    """Load TEST_ONLY only after a detached owner approval binds the entire release."""
    root = Path(root)
    folder = root/'demo/public'
    if not (folder/'review.json').is_file():
        return None
    if folder.is_symlink() or folder.parent.is_symlink():
        raise ReplayError('公开包目录不能是符号链接')
    review = read_json(folder/'review.json')
    try:
        if review.get('status') == 'OWNER_REVIEW' and not (root/'OWNER_APPROVAL.json').exists():
            # Pending owner review is an honest empty state, never a public rendering.
            from .owner_review import validate_dossier
            validate_dossier(root, review)
            return None
        review = approved_review(root, review)
    except (ValueError, KeyError, TypeError) as error:
        raise ReplayError('所有者审核记录、公开范围或最终哈希不符') from error
    payload = folder/'payload.json'
    packet = read_json(payload)
    raw = payload.read_bytes()
    if hashlib.sha256(raw).hexdigest() != review.get('payload_sha256'):
        raise ReplayError('公开包与批准哈希不一致')
    if PRIVATE_PATTERN.search(raw.decode('utf-8')) or PRIVATE_PATTERN.search(json.dumps(review)):
        raise ReplayError('公开包包含禁止字段或私有路径')
    if set(packet) != {'summary', 'task', 'responses', 'original_guard', 'posthoc_review'}:
        raise ReplayError('公开包字段不符合最小展示契约')
    summary, task = packet['summary'], packet['task']
    if (summary.get('client_mode') != 'TEST_ONLY' or task.get('client_mode') != 'TEST_ONLY'
            or task.get('provider') != 'TEST_ONLY' or summary.get('task_id') != task.get('id')
            or any(r.get('model') != 'TEST_ONLY' for r in packet['responses'])
            or packet['posthoc_review'] is not None or task.get('M5_enabled') is not False):
        raise ReplayError('合成轨迹必须全程 TEST_ONLY；不得借用真实复评或模型身份')
    validate_packet(packet)
    return {**packet, 'mode': 'TEST_ONLY', 'directory': folder, 'planning_response': None,
            'public_review': review}


def story_view(packet):
    """Join using saved IDs and exact formula/family, never rerun chemistry."""
    task = packet['task']
    view = saved_run_view(task, packet['responses'])
    calls = task.get('calls', [])
    sources = {s['record_id']: s for s in view['sources']}
    generated = [c for call in calls if call['name'] == 'generate_material_candidates'
                 and call['output']['status'] == 'OK' for c in call['output']['data'].get('candidates', [])]
    details = []
    for row, candidate in zip(view['candidates'], generated):
        receipts = [c for c in calls if c['id'] == row['化学收据']]
        receipt = receipts[0] if len(receipts) == 1 else None
        seed_ids = sorted({i for s in candidate.get('generation_sources', []) for i in s.get('seed_source_ids', [])})
        linked = [sources[i] for i in seed_ids if i in sources]
        details.append({'row': row, 'candidate': candidate, 'receipt': receipt,
                        'sources': linked, 'missing_source_ids': sorted(set(seed_ids)-sources.keys())})
        row['种子DOI'] = ' / '.join(s['DOI'] for s in linked if s.get('DOI')) or 'UNKNOWN'
    timeline = [{'顺序': i, '模型选择的工具': c['name'], '执行状态': c['output']['status'],
                 '收据耗时秒': c.get('elapsed_seconds'), 'call_id': c['id']}
                for i, c in enumerate(calls, 1)]
    if packet.get('mode') == 'TEST_ONLY':
        for row in [*view['candidates'], *view['sources']]:
            row['证据模式'] = 'TEST_ONLY'
        timeline = [{**row, '执行状态': 'TEST_ONLY_SIMULATED_NOT_EXECUTED',
                     'TEST_ONLY 模拟开始秒': call.get('simulated_start_seconds'),
                     'TEST_ONLY 模拟结束秒': call.get('simulated_end_seconds'),
                     'TEST_ONLY 选择说明': call.get('selection_reason')}
                    for row, call in zip(timeline, calls)]
    return {**view, 'details': details, 'timeline': timeline}
