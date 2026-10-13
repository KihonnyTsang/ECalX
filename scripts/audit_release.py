"""Read-only heuristic scan and hash verification; never prints matched secret values."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    'credential_like': re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b|-----BEGIN [A-Z ]*PRIVATE KEY'),
    'local_path': re.compile(r'(?:/Users/|/home/)[^\s\"\'<>]+|[A-Za-z]:[\\/](?:Users|Documents)[\\/][^\s]+'),
    'presigned_url': re.compile(r'https?://[^\s\"\'<>]*(?:[?&](?:X-Amz-[^=]*|X-Goog-[^=]*|Signature|sig|access_token)=)[^\s\"\'<>]*', re.I),
    'assigned_secret': re.compile(r'(?i)(?:api_key|access_token|secret_key|password)\s*[=:]\s*[\"\']([^\"\'\n]{8,})'),
    'private_contact': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}
BLOCKED_DIRS = {'.git','.venv','.agents','.codex','.aws','data','models','reports','__pycache__','build','dist'}
BLOCKED_SUFFIXES = {'.sqlite','.sqlite3','.db','.pyc','.joblib','.pkl','.pickle','.pt','.pth','.pdf','.csv','.xlsx','.jpg','.png'}


def scan():
    hits = []
    paths = []
    literal_reviews = json.loads((ROOT/'audit/literal_reviews.json').read_text()) if (ROOT/'audit/literal_reviews.json').exists() else []
    for path in sorted(ROOT.rglob('*')):
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            hits.append({'path':relative,'rule':'symlink','line':None}); continue
        if path.is_dir():
            if path.name in BLOCKED_DIRS or path.suffix == '.egg-info':
                hits.append({'path':relative,'rule':'excluded_directory','line':None})
            continue
        if not path.is_file():
            hits.append({'path':relative,'rule':'special_file','line':None}); continue
        paths.append(relative)
        if path.name.startswith('.env') or path.suffix.lower() in BLOCKED_SUFFIXES or path.name.endswith(('-wal','-shm','-journal')):
            hits.append({'path':relative,'rule':'excluded_asset','line':None})
        raw = path.read_bytes()
        text = raw.decode('utf-8',errors='strict')
        sha = hashlib.sha256(raw).hexdigest()
        for line, value in enumerate(text.splitlines(),1):
            for name, regex in RULES.items():
                matches=list(regex.finditer(value))
                if not matches: continue
                if name=='assigned_secret' and all(m.group(1).startswith('TEST_ONLY') for m in matches):
                    continue
                disposition = 'REVIEW'
                if any(r['path']==relative and r['line']==line and r['rule']==name and r['sha256']==sha for r in literal_reviews):
                    disposition='REVIEWED_CODE_LITERAL'
                hits.append({'path':relative,'line':line,'rule':name,'disposition':disposition})
    digest = ROOT/'audit/SHA256SUMS'
    hash_errors=[]
    hashed_paths=[]
    if digest.exists():
        for line in digest.read_text().splitlines():
            expected, relative=line.split('  ',1)
            hashed_paths.append(relative)
            path=ROOT/relative
            if not path.resolve().is_relative_to(ROOT.resolve()):
                hash_errors.append(relative); continue
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                hash_errors.append(relative)
    actual=set(paths)
    if 'OWNER_APPROVAL.json' in actual:
        sys.path.insert(0, str(ROOT/'src'))
        from ecalx.presentation.owner_review import approved_review
        try:
            approved_review(ROOT, json.loads((ROOT/'demo/public/review.json').read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            hash_errors.append('INVALID_OWNER_APPROVAL_RECEIPT')
        actual.remove('OWNER_APPROVAL.json')
    if set(hashed_paths) != actual-{'audit/SHA256SUMS'} or len(hashed_paths) != len(set(hashed_paths)):
        hash_errors.append('SHA256_COVERAGE_OR_DUPLICATE_ERROR')
    whitelist=ROOT/'audit/release_whitelist.tsv'
    whitelist_errors=[]
    if whitelist.exists():
        listed={line.split('\t')[0] for line in whitelist.read_text().splitlines()[1:]}
        whitelist_errors=sorted(actual.symmetric_difference(listed))
    unresolved=[h for h in hits if h.get('disposition')!='REVIEWED_CODE_LITERAL']
    return {'status':'PASSED_HEURISTIC_SCAN' if not (unresolved or hash_errors or whitelist_errors) else 'REVIEW_REQUIRED',
            'files_scanned':len(paths),'hits':hits,'unresolved_count':len(unresolved),
            'hash_errors':hash_errors,'whitelist_errors':whitelist_errors,
            'limitations':['Regex scan is not independent human security or legal review',
                           'Does not prove absence of all secrets, unmarked copying or arbitrary scientific claims',
                           'No Git history or binary images included; no dependency binaries scanned here']}


if __name__ == '__main__':
    result=scan()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    sys.exit(0 if result['status']=='PASSED_HEURISTIC_SCAN' else 1)
