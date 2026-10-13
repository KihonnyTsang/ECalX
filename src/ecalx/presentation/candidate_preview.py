"""Explicit local TEST_ONLY review preview. Never used by the public B loader."""
import hashlib
from pathlib import Path

from .replay import PRIVATE_PATTERN, ReplayError, read_json
from .owner_review import validate_dossier
from .test_only import validate_packet


def load_candidate(folder):
    folder = Path(folder)
    if folder.is_symlink() or folder.parent.is_symlink():
        raise ReplayError('TEST_ONLY 候选目录不能为链接')
    review = read_json(folder/'review.json')
    payload = folder/'payload.json'
    packet = read_json(payload)
    raw = payload.read_bytes()
    if review.get('schema_version') == 2:
        validate_dossier(folder.parent.parent, review)
    else:
        if (review.get('status') != 'PENDING_INDEPENDENT_REVIEW'
                or review.get('rights_review') != 'PENDING'
                or review.get('privacy_review') != 'PENDING'
                or any(review.get(k) for k in ('independent_reviewer', 'owner_approval', 'reviewed_at', 'review_record'))
                or hashlib.sha256(raw).hexdigest() != review.get('payload_sha256')):
            raise ReplayError('TEST_ONLY 本地预览只接受哈希一致、无人代签的待审包')
    if PRIVATE_PATTERN.search(raw.decode('utf-8')):
        raise ReplayError('TEST_ONLY 候选含禁止字段')
    if (set(packet) != {'task', 'summary', 'responses', 'original_guard', 'posthoc_review'}
            or packet['summary'].get('client_mode') != 'TEST_ONLY'
            or packet['task'].get('client_mode') != 'TEST_ONLY'
            or packet['task'].get('provider') != 'TEST_ONLY'
            or packet['task'].get('M5_enabled') is not False
            or packet['summary'].get('task_id') != packet['task'].get('id')
            or any(r.get('model') != 'TEST_ONLY' for r in packet['responses'])
            or packet['original_guard'] is not None or packet['posthoc_review'] is not None):
        raise ReplayError('TEST_ONLY 本地预览身份或关联不符')
    validate_packet(packet)
    return {**packet, 'mode': 'TEST_ONLY', 'directory': folder, 'planning_response': None,
            'public_review': review, 'local_preview': True}
