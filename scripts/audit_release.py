"""Audit the explicit public source inventory; never read credentials/workspaces.

--write freezes manifests after scanning. It does not approve publication.
Generated files have no timestamps, paths outside this checkout, or self-hashes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = {'audit/public_files.txt', 'audit/SHA256SUMS'}
ROOT_FILES = {'.gitignore', 'LICENSE', 'README.md', 'app.py', 'pyproject.toml', 'requirements-tested.txt'}
PUBLIC_DIRS = {'src', 'ecalx_local', 'tests', 'examples', 'docs', 'scripts', 'audit', '.streamlit'}
GENERATED = {'.git', '.venv', 'build', 'dist', '.pytest_cache', '__pycache__'}
SUFFIXES = {'.py', '.md', '.toml', '.csv', '.json', '.css', '.txt'}
FAKE_KEYS = {
    'tests/test_byo.py': {'sk-BYO_FAKE_KEY_NEVER_SENT'},
    'tests/test_http_contract.py': {'sk-TEST_HTTP_ONLY_NEVER_SENT'},
}
KEY_PATTERN = re.compile(r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{12,}')
PATH_PATTERN = re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/')
SECRET_ASSIGNMENT = re.compile(r'''(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*["']([A-Za-z0-9_./+-]{16,})["']''')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def sensitive_literals(rel, raw):
    errors = []
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        return [rel + ': non-text public file']
    if PATH_PATTERN.search(content):
        errors.append(rel + ': personal absolute path')
    allowed = FAKE_KEYS.get(rel, set())
    if rel in {'scripts/audit_release.py', 'audit/security_scan.json'}:
        allowed = set().union(*FAKE_KEYS.values())
    if any(key not in allowed for key in KEY_PATTERN.findall(content)):
        errors.append(rel + ': unapproved key literal')
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', content):
        errors.append(rel + ': private key')
    for value in SECRET_ASSIGNMENT.findall(content):
        if value not in allowed:
            errors.append(rel + ': secret assignment')
    if re.search(r'github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}', content):
        errors.append(rel + ': credential token')
    return errors


def inventory():
    files, errors = {}, []
    for p in sorted(ROOT.rglob('*')):
        parts = p.relative_to(ROOT).parts
        if any(part in GENERATED or part.endswith('.egg-info') for part in parts):
            continue
        rel = '/'.join(parts)
        if p.is_symlink():
            errors.append(rel + ': symlink'); continue
        if not p.is_file():
            continue
        if rel not in ROOT_FILES and parts[0] not in PUBLIC_DIRS:
            errors.append(rel + ': outside public scope')
        if rel not in ROOT_FILES and rel not in MANIFESTS and p.suffix not in SUFFIXES:
            errors.append(rel + ': unsupported public file type')
        if any(part in {'data', 'models', 'raw', 'var', 'tasks', 'config'} for part in parts[:-1]) and parts[0] != 'src':
            errors.append(rel + ': private asset directory')
        if p.name.startswith('.env') or p.name in {'OWNER_APPROVAL.json', '.DS_Store'}:
            errors.append(rel + ': forbidden file')
        raw = p.read_bytes()
        errors.extend(sensitive_literals(rel, raw))
        files[rel] = digest(raw)
    # An ignored build/workspace file must never sneak into a Git commit.
    if (ROOT/'.git').exists():
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        errors.extend(rel + ': tracked generated/private file' for rel in tracked if rel and (ROOT/rel).exists() and rel not in files)
    return files, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    files, errors = inventory()
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    package = (ROOT/'src/ecalx/__init__.py').read_text()
    if version != '0.2.0b1' or '__version__ = "0.2.0b1"' not in package:
        errors.append('project/package version mismatch')
    report = {'version': version, 'status': 'PASS' if not errors else 'FAIL',
              'scanned_public_files': len(set(files) | MANIFESTS | {'audit/security_scan.json'}),
              'errors': errors, 'fake_key_exceptions': FAKE_KEYS,
              'online_acceptance': 'NOT_RUN', 'owner_approval': 'PENDING_FINAL_CONTENT_CONFIRMATION',
              'scope': 'public source tree; generated directories excluded and checked against Git index',
              'personal_assets_distributed': False, 'publication_authorized': False}
    report['fake_key_exceptions'] = {k: sorted(v) for k, v in FAKE_KEYS.items()}
    if args.write and not errors:
        path = ROOT/'audit/security_scan.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        files['audit/security_scan.json'] = digest(path.read_bytes())
        (ROOT/'audit/public_files.txt').write_text('\n'.join(sorted(set(files) | MANIFESTS))+'\n')
        (ROOT/'audit/SHA256SUMS').write_text(''.join(files[rel]+'  '+rel+'\n' for rel in sorted(files) if rel not in MANIFESTS))
    elif not errors:
        try:
            expected = (ROOT/'audit/public_files.txt').read_text().splitlines()
            if expected != sorted(files):
                errors.append('public inventory mismatch')
            sums = {rel: sha for sha, rel in (line.split('  ', 1) for line in (ROOT/'audit/SHA256SUMS').read_text().splitlines())}
            if sums != {rel: sha for rel, sha in files.items() if rel not in MANIFESTS}:
                errors.append('SHA256 mismatch')
        except (OSError, ValueError):
            errors.append('missing/invalid release manifests; freeze with --write')
    print(json.dumps({'version': version, 'status': 'FAIL' if errors else 'PASS',
                      'files': report['scanned_public_files'], 'errors': errors,
                      'online_acceptance': 'NOT_RUN'}, ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
