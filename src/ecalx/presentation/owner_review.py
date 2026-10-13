"""Owner publication attestations for TEST_ONLY; never scientific certification.

The unsigned dossier and release stay immutable. A detached OWNER_APPROVAL.json
records the owner's actual final confirmation and binds every released file.
Attestations are auditable statements, not authenticated digital signatures.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath


MANIFEST = 'audit/SHA256SUMS'
RECEIPT = 'OWNER_APPROVAL.json'


def runtime_artifact(relative):
    """Local install/checkout metadata is never part of the publication scope.

    The release scanner separately forbids these in a frozen distribution.
    """
    return any(part in {'.git', '.venv', '__pycache__', '.pytest_cache', 'build'}
               or part.endswith('.egg-info') for part in relative.parts)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_file(root, relative):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Release root cannot be a symbolic link')
    # macOS /var is a system alias. Reject links within the release boundary.
    root = root.resolve()
    if not isinstance(relative, str):
        raise ValueError('Invalid public path')
    path = PurePosixPath(relative)
    if (not relative or path.is_absolute() or '..' in path.parts
            or path.as_posix() != relative or '\\' in relative):
        raise ValueError('Invalid public path')
    target = root / relative
    if any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(root)):
        raise ValueError('Public files cannot be symbolic links')
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Public file missing or outside release')
    return target


def validate_dossier(root, review):
    if (review.get('schema_version') != 2 or review.get('status') != 'OWNER_REVIEW'
            or review.get('evidence_mode') != 'TEST_ONLY'
            or review.get('review_basis') != 'OWNER_REVIEW'
            or not isinstance(review.get('owner'), str) or not review['owner'].strip()
            or not isinstance(review.get('content_author'), str) or not review['content_author'].strip()
            or review.get('rights_review') != 'PENDING'
            or review.get('privacy_review') != 'PENDING'
            or any(review.get(k) for k in ('owner_approval', 'reviewed_at', 'review_record', 'independent_reviewer'))):
        raise ValueError('Unsigned TEST_ONLY owner review dossier required')
    scope = review.get('public_scope')
    if not isinstance(scope, list) or not scope:
        raise ValueError('Explicit public scope required')
    paths = []
    for item in scope:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
            raise ValueError('Public scope must list explicit file hashes')
        path = item['path']
        if digest(safe_file(root, path)) != item['sha256']:
            raise ValueError('Owner review scope hash mismatch')
        paths.append(path)
    if len(paths) != len(set(paths)) or 'demo/public/payload.json' not in paths:
        raise ValueError('Duplicate scope or missing TEST_ONLY payload')
    if digest(safe_file(root, 'demo/public/payload.json')) != review.get('payload_sha256'):
        raise ValueError('Owner review payload hash mismatch')
    return review


def approved_review(root, review):
    """Validate actual owner receipt, complete release scope and all final bytes."""
    root = Path(root)
    validate_dossier(root, review)
    receipt = json.loads(safe_file(root, RECEIPT).read_text(encoding='utf-8'))
    if (receipt.get('schema_version') != 2 or receipt.get('status') != 'OWNER_APPROVED'
            or receipt.get('review_basis') != 'OWNER_REVIEW'
            or receipt.get('evidence_mode') != 'TEST_ONLY'
            or receipt.get('owner') != review['owner']
            or receipt.get('payload_sha256') != review['payload_sha256']
            or receipt.get('rights_review') != 'APPROVED'
            or receipt.get('privacy_review') != 'APPROVED'
            or any(not isinstance(receipt.get(k), str) or not receipt[k].strip()
                   for k in ('owner_approval', 'reviewed_at', 'review_record'))
            or receipt.get('independent_reviewer')):
        raise ValueError('Actual TEST_ONLY owner approval record required')
    if datetime.fromisoformat(receipt['reviewed_at']).utcoffset() is None:
        raise ValueError('Actual owner review time must include its timezone')
    manifest = safe_file(root, MANIFEST)
    if digest(manifest) != receipt.get('release_manifest_sha256'):
        raise ValueError('Owner approval does not bind this release manifest')
    paths = []
    for line in manifest.read_text(encoding='utf-8').splitlines():
        expected, relative = line.split('  ', 1)
        if relative in (RECEIPT, MANIFEST) or digest(safe_file(root, relative)) != expected:
            raise ValueError('Approved release bytes changed')
        paths.append(relative)
    scope = receipt.get('public_scope')
    expected_scope = set(paths) | {MANIFEST}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*')
              if p.is_file() and not runtime_artifact(p.relative_to(root))}
    if (len(paths) != len(set(paths)) or not isinstance(scope, list)
            or not all(isinstance(path, str) for path in scope)
            or len(scope) != len(set(scope)) or set(scope) != expected_scope
            or actual != expected_scope | {RECEIPT}
            or 'demo/public/review.json' not in paths
            or any(item['path'] not in paths for item in review['public_scope'])):
        raise ValueError('Owner approval public scope differs from final release')
    return {**review, **receipt}
