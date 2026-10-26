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
ROOT_FILES = {'.gitignore', 'LICENSE', 'README.md', 'CHANGELOG.md', 'app.py', 'pyproject.toml', 'requirements-tested.txt', 'requirements-paper-mvp-tested.txt', 'MANIFEST.in'}
PUBLIC_DIRS = {'src', 'ecalx_local', 'tests', 'examples', 'docs', 'scripts', 'audit', '.streamlit'}
GENERATED = {'.git', '.venv', 'build', 'dist', '.pytest_cache', '__pycache__'}
SUFFIXES = {'.py', '.md', '.toml', '.csv', '.json', '.css', '.txt'}
SCREENSHOTS = {'docs/screenshots/' + name for name in (
    '01-workbench-home.jpg', '02-goal-and-elements.jpg',
    '03-generation-chemical-checks.jpg', '04-virtual-experiment-ei.jpg',
    '05-bst-delta-t.jpg', '06-candidate-results.jpg', '07-research-report.jpg')}
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
    screenshot_code = {}
    try:
        review = json.loads((ROOT/'audit/screenshots.json').read_text())
        screenshot_hashes = {r['file']: r['sha256'] for r in review['screenshots']}
        screenshot_code = review['code_sha256']
        if set(screenshot_hashes) != SCREENSHOTS:
            errors.append('screenshot inventory mismatch')
    except (OSError, KeyError, TypeError, ValueError):
        screenshot_hashes = {}
        errors.append('missing/invalid screenshot review manifest')
    for p in sorted(ROOT.rglob('*')):
        parts = p.relative_to(ROOT).parts
        if any(part in GENERATED or part.endswith('.egg-info') for part in parts):
            continue
        rel = '/'.join(parts)
        if p.is_symlink():
            errors.append(rel + ': symlink'); continue
        if not p.is_file():
            continue
        # Setuptools adds these text-only envelope files to an sdist. They are
        # not frozen source files, but must still pass the sensitive-text scan.
        if not (ROOT/'.git').exists() and rel in {'PKG-INFO', 'setup.cfg'}:
            errors.extend(sensitive_literals(rel, p.read_bytes()))
            continue
        if rel not in ROOT_FILES and parts[0] not in PUBLIC_DIRS:
            errors.append(rel + ': outside public scope')
        if rel not in ROOT_FILES and rel not in MANIFESTS and p.suffix not in SUFFIXES and rel not in SCREENSHOTS:
            errors.append(rel + ': unsupported public file type')
        if any(part in {'data', 'models', 'raw', 'var', 'tasks', 'config'} for part in parts[:-1]) and parts[0] != 'src':
            errors.append(rel + ': private asset directory')
        if p.name.startswith('.env') or p.name in {'OWNER_APPROVAL.json', '.DS_Store'}:
            errors.append(rel + ': forbidden file')
        raw = p.read_bytes()
        if rel in SCREENSHOTS:
            if digest(raw) != screenshot_hashes.get(rel):
                errors.append(rel + ': screenshot checksum mismatch')
            if not raw.startswith(b'\xff\xd8\xff') or not raw.endswith(b'\xff\xd9') or len(raw) > 2_000_000:
                errors.append(rel + ': invalid/oversized JPEG')
            if b'Exif\x00\x00' in raw or b'http://ns.adobe.com/xap/' in raw:
                errors.append(rel + ': image metadata must be removed')
        else:
            errors.extend(sensitive_literals(rel, raw))
        files[rel] = digest(raw)
    runtime_files = {rel: sha for rel, sha in files.items()
                     if rel.startswith(('ecalx_local/', 'src/')) and Path(rel).suffix in {'.py', '.css', '.csv'}}
    if screenshot_code != runtime_files:
        errors.append('screenshots do not match current runtime source')
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
    if version != '0.2.0b3' or '__version__ = "0.2.0b3"' not in package:
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
