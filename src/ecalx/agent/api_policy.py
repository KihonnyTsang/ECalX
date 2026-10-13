"""Trusted host policy. No grant/budget/ledger mutators are model tools.

SQLite BEGIN IMMEDIATE reserves before send, across processes and task IDs.
Missing ledgers fail closed. Only explicit installation can create one.
"""
import hashlib
import json
import math
import os
import sqlite3
import stat
import signal
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import MODEL, BASE_URL
from .llm import ProviderError

ROOT = Path(__file__).resolve().parents[3]
# No historical owner authorization is distributed or inherited by this release.
CAPS = dict(task_requests=20, task_tool_calls=20, output_tokens=1024, task_tokens=262144,
            request_seconds=75, task_seconds=180, daily_requests=1000,
            daily_tokens=50000000, daily_estimated_cny=50)
LEGACY_RISK_IDS = frozenset()
MODEL_QUALITY_ERRORS = frozenset({'PLAN_REJECTED', 'NO_TOOL_CALLS', 'UNEXPECTED_TOOL_CALLS',
                                'INVALID_TOOL_CALL', 'INCOMPLETE_RESPONSE'})


def receipt_digest(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def accepted_legacy_risks(db, rows):
    """Only immutable receipts named by the explicit host acceptance, never new unknowns."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='policy_events'").fetchone():
        return set()
    events = db.execute("SELECT detail FROM policy_events WHERE action='HOST_LEGACY_RISK_ACCEPTED'").fetchall()
    if not events:
        return set()
    if len(events) != 1:
        fail('LEDGER_RISK_ACCEPTANCE_INVALID')
    detail = json.loads(events[0]['detail'])
    fingerprints = detail.get('receipt_fingerprints', {})
    selected = {r['id']: r for r in rows if r['id'] in LEGACY_RISK_IDS}
    if (set(fingerprints) != LEGACY_RISK_IDS or set(selected) != LEGACY_RISK_IDS or
            detail.get('scope') != 'EXISTING_ECALX_DEVELOPMENT_GRANT' or
            not detail.get('human_confirmation') or
            any(r['status'] != 'LEGACY' or r['actual_tokens'] is not None or
                receipt_digest(r) != fingerprints[rid] for rid, r in selected.items())):
        fail('LEDGER_RISK_ACCEPTANCE_INVALID')
    return set(selected)

AUDIT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS policy_events(seq INTEGER PRIMARY KEY, time TEXT, action TEXT, detail TEXT);
CREATE TRIGGER IF NOT EXISTS policy_events_no_update BEFORE UPDATE ON policy_events
BEGIN SELECT RAISE(ABORT,'policy audit is append only'); END;
CREATE TRIGGER IF NOT EXISTS policy_events_no_delete BEFORE DELETE ON policy_events
BEGIN SELECT RAISE(ABORT,'policy audit is append only'); END;
'''


def audit_event(db, action, detail):
    # Existing v1 ledgers need the explicit host migration before audited recovery.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='policy_events'").fetchone():
        db.execute('INSERT INTO policy_events(time,action,detail) VALUES(?,?,?)',
                   (datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), action, json.dumps(detail)))


def known_complete(attempt):
    u = attempt.get('usage', {})
    return (attempt.get('status') == 'RECEIVED' and attempt.get('response_received') is True and
            attempt.get('actual_model') == MODEL and not attempt.get('error_code') and
            all(type(u.get(k)) is int and u[k] >= 0 for k in ('input_tokens','output_tokens','total_tokens')) and
            u['input_tokens'] + u['output_tokens'] == u['total_tokens'])


def ledger_anomalies(rows):
    """Validate accounting before trusting sums; malformed entries never buy quota."""
    for r in rows:
        if (r['status'] not in {'INFLIGHT','FAILED','RECEIVED','LEGACY'} or
                any(type(r[k]) is not int or r[k] < 0 for k in ('reserved','accounted')) or
                type(r['reserved_cost']) not in (int,float) or not math.isfinite(r['reserved_cost']) or r['reserved_cost'] < 0):
            return True
        if r['actual_tokens'] is None:
            if r['accounted'] != r['reserved'] or r['cost'] is not None:
                return True
        elif (type(r['actual_tokens']) is not int or r['actual_tokens'] < 0 or
              r['accounted'] != r['actual_tokens'] or type(r['cost']) not in (int,float) or
              not math.isfinite(r['cost']) or r['cost'] < 0):
            return True
    return False


def failure_class(reason, attempts=()):
    if reason.startswith(('AUTHORIZATION_', 'EGRESS_', 'LEDGER_', 'POLICY_', 'SOURCE_', 'REDIRECT_', 'OUTPUT_TOKEN_', 'TASK_BUDGET_')):
        return 'A_SECURITY_REVIEW'
    if reason in MODEL_QUALITY_ERRORS and attempts and all(
            a.get('error_code') in (None, reason) and known_complete({**a, 'error_code':None})
            for a in attempts):
        return 'C_MODEL_QUALITY'
    if (reason in {'UNKNOWN_USAGE','UNKNOWN_INFLIGHT_REQUEST','UNRESOLVED_UNKNOWN_USAGE',
                   'REQUEST_TIMEOUT','READ_TIMEOUT','CONNECTION_ERROR','HTTP_ERROR','TIMEOUT',
                   'RESERVATION_EXCEEDED','USAGE_INCONSISTENT','TOKEN_LIMIT','REQUEST_LIMIT',
                   'REQUEST_OR_TOKEN_BUDGET_LIMIT','DAILY_ESTIMATED_COST_LIMIT',
                   'TASK_STOPPED_OR_TIME_LIMIT','TASK_TIME_LIMIT'} or
            (attempts and not all(known_complete(a) for a in attempts))):
        return 'B_USAGE_RISK'
    return 'A_SECURITY_REVIEW'


def fail(code):
    raise ProviderError('项目API策略停止：' + code, code=code)


def read_json(path):
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                fail('POLICY_INVALID')
            raw = handle.read(65537)
        if len(raw) > 65536:
            fail('POLICY_INVALID')
        return raw, json.loads(raw)
    except (OSError, ValueError):
        fail('POLICY_INVALID')


def settings(root=ROOT, now=None, *, authorization_sha256=None):
    if not isinstance(authorization_sha256, str) or len(authorization_sha256) != 64:
        fail('AUTHORIZATION_NOT_CONFIGURED')
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    raw, grant = read_json(Path(root)/'config/api_authorization.json')
    if hashlib.sha256(raw).hexdigest() != authorization_sha256:
        fail('AUTHORIZATION_INVALID')
    _, cfg = read_json(Path(root)/'config/api_budget.json')
    if cfg.get('automatic_api_enabled') is not True:
        fail('AUTOMATIC_API_DISABLED')
    for key, cap in CAPS.items():
        value = cfg.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= cap
                or (key not in {'request_seconds','task_seconds','daily_estimated_cny'}
                    and type(value) is not int)):
            fail('BUDGET_CONFIG_INVALID')
    prices = cfg.get('prices', {})
    if (prices.get('model') != MODEL or prices.get('source') != 'https://www.siliconflow.cn/pricing'
            or prices.get('timezone') != 'Asia/Shanghai'
            or not prices.get('verified_on', '') <= now.date().isoformat() <= prices.get('valid_through', '')):
        fail('PRICE_VERIFICATION_REQUIRED')
    for period in ('peak', 'off_peak'):
        if any(type(prices.get(period, {}).get(k)) not in (int, float)
               or not 0 < prices[period][k] <= 10000 for k in
               ('input_per_million', 'output_per_million', 'cached_input_per_million')):
            fail('PRICE_VERIFICATION_REQUIRED')
    return grant, cfg


def initialize_ledger(root=ROOT, legacy=()):
    """One-time trusted installation. Never used by request path; never overwrite."""
    root = Path(root)
    directory = root/'data/api_usage'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/'ledger.sqlite3'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE tasks(id TEXT PRIMARY KEY, started REAL, stopped INTEGER DEFAULT 0);
            CREATE TABLE requests(id TEXT PRIMARY KEY, task TEXT, day TEXT, status TEXT,
                reserved INTEGER, accounted INTEGER, actual_tokens INTEGER, cost REAL,
                reserved_cost REAL, payload_sha TEXT, payload_bytes INTEGER, audit TEXT);
            CREATE TABLE control(id INTEGER PRIMARY KEY CHECK(id=1), halted INTEGER, reason TEXT);
            INSERT INTO control VALUES(1,0,NULL);
        ''')
        for row in legacy:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', row)
        db.executescript(AUDIT_SCHEMA)
        audit_event(db, 'LEDGER_INSTALLED', {'legacy_entries':len(legacy)})
    return path


def budget_status(root=ROOT, *, authorization_sha256=None):
    """Human-facing read-only daily totals, including carried UNKNOWN reservations."""
    _, cfg = settings(root, authorization_sha256=authorization_sha256)
    day = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    policy = ProjectAPIPolicy('HOST_BUDGET_DISPLAY', None, root)
    with policy.connect_readonly() as db:
        db.execute('BEGIN')
        rows = db.execute('SELECT * FROM requests').fetchall()
        if ledger_anomalies(rows):
            fail('LEDGER_ACCOUNTING_ANOMALY')
        from .api_reconciliation import effective_usage
        effective = effective_usage(db, rows)
        accepted = accepted_legacy_risks(db, rows)
        daily = [r for r in effective if r['day']==day]
        charges = daily + [r for r in effective if r['id'] in accepted and
                          r['actual_tokens'] is None and r['day']!=day]
        control = dict(db.execute('SELECT * FROM control WHERE id=1').fetchone())
    return {'day':day, 'daily_requests_used':len(daily),
        'daily_requests_limit':cfg['daily_requests'],
        'daily_tokens_accounted':sum(r['accounted'] for r in charges),
        'daily_tokens_limit':cfg['daily_tokens'],
        'daily_estimated_cny_charge':sum(r['cost'] if r['cost'] is not None else
            r['reserved_cost'] for r in charges), 'daily_estimated_cny_limit':cfg['daily_estimated_cny'],
        'unknown_usage_requests':sum(r['actual_tokens'] is None for r in charges),
        'task_requests_limit':cfg['task_requests'], 'task_tool_calls_limit':cfg['task_tool_calls'],
        'output_tokens_limit':cfg['output_tokens'], 'request_seconds':cfg['request_seconds'],
        'task_seconds':cfg['task_seconds'], 'automatic_retries':0,
        'task_tokens_limit':cfg['task_tokens'], 'control':control,
        'cost_notice':'本地估算及未知用量预留；不是SiliconFlow实际账单硬上限。历史UNKNOWN实际费用仍未知。'}


class ProjectAPIPolicy:
    def __init__(self, task_id, validator, root=ROOT, *, authorization_sha256=None):
        self.authorization_sha256 = authorization_sha256
        self.root = Path(root)
        self.task_id = task_id
        self.validator = validator
        self.path = self.root/'data/api_usage/ledger.sqlite3'
        self.monotonic_start = time.monotonic()

    def connect_readonly(self):
        if not self.path.is_file() or self.path.is_symlink():
            fail('LEDGER_MISSING_OR_INVALID')
        db = sqlite3.connect('file:' + str(self.path) + '?mode=ro', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def connect(self):
        if not self.path.is_file() or self.path.is_symlink():
            fail('LEDGER_MISSING_OR_INVALID')
        db = sqlite3.connect('file:' + str(self.path) + '?mode=rw', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def halt(self, reason, attempts=()):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            category = failure_class(reason, attempts)
            control = db.execute('SELECT * FROM control WHERE id=1').fetchone()
            # Never replace an existing global stop with a secondary/wrapped error.
            if category != 'C_MODEL_QUALITY' and not control['halted']:
                db.execute('UPDATE control SET halted=1,reason=? WHERE id=1',(reason,))
            db.execute('UPDATE tasks SET stopped=1 WHERE id=?',(self.task_id,))
            audit_event(db, 'TASK_FAILED', {'task':self.task_id, 'reason':reason, 'category':category,
                                          'global_halt_preserved':bool(control['halted'])})

    def before_send(self, client, raw, timeout):
        if threading.current_thread() is not threading.main_thread() or signal.getitimer(signal.ITIMER_REAL)[0]:
            fail('WALL_DEADLINE_UNAVAILABLE_USE_MAIN_PROCESS_CLI')
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        grant, cfg = settings(self.root, now, authorization_sha256=self.authorization_sha256)
        if (client.provider != grant['provider'] or client.model != grant['model']
                or client.base_url+'/chat/completions' != grant['endpoint']
                or client.budget.transport_retries != 0):
            fail('AUTHORIZATION_SCOPE_REJECTED')
        # This callback compares the complete encoded body against the trusted,
        # current legal context, including verified fact projections and schemas.
        self.validator(raw)
        payload = json.loads(raw)
        if payload['max_tokens'] > cfg['output_tokens']:
            fail('OUTPUT_TOKEN_LIMIT')
        for key, local in (('llm_requests','task_requests'), ('tool_calls','task_tool_calls'),
                           ('total_tokens','task_tokens'),
                           ('request_seconds','request_seconds'), ('seconds','task_seconds')):
            if getattr(client.budget, key) > cfg[local]:
                fail('TASK_BUDGET_LIMIT')
        reserve = len(raw)+1024+payload['max_tokens']
        # Reserve with verified worst period and no assumed cache discount.
        rate = max(v for p in ('peak', 'off_peak') for v in cfg['prices'][p].values())
        reserved_cost = reserve*rate/1_000_000
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            control = db.execute('SELECT * FROM control WHERE id=1').fetchone()
            if control['halted']:
                fail('PROJECT_HALTED_' + control['reason'])
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='policy_events'").fetchone():
                fail('POLICY_AUDIT_MIGRATION_REQUIRED')
            original_rows = db.execute('SELECT * FROM requests').fetchall()
            if ledger_anomalies(original_rows):
                fail('LEDGER_ACCOUNTING_ANOMALY')
            from .api_reconciliation import effective_usage
            effective_rows = effective_usage(db, original_rows)
            accepted = accepted_legacy_risks(db, original_rows)
            # Any crash leaves a durable in-flight reservation and blocks all new IDs.
            if db.execute("SELECT 1 FROM requests WHERE status='INFLIGHT'").fetchone():
                fail('UNKNOWN_INFLIGHT_REQUEST')
            if any(r['actual_tokens'] is None and r['id'] not in accepted for r in effective_rows):
                fail('UNRESOLVED_UNKNOWN_USAGE')
            db.execute('INSERT OR IGNORE INTO tasks(id,started) VALUES(?,?)', (self.task_id,time.time()))
            task = db.execute('SELECT * FROM tasks WHERE id=?', (self.task_id,)).fetchone()
            elapsed = max(time.time()-task['started'], time.monotonic()-self.monotonic_start)
            if task['stopped'] or elapsed >= min(cfg['task_seconds'],client.budget.seconds):
                fail('TASK_STOPPED_OR_TIME_LIMIT')
            left = min(timeout, cfg['request_seconds'], cfg['task_seconds']-elapsed,
                       client.budget.seconds-elapsed)
            if left <= 0:
                fail('TASK_TIME_LIMIT')
            for column, value, ncap, tcap, ccap in (
                ('task',self.task_id,min(cfg['task_requests'],client.budget.llm_requests),
                 min(cfg['task_tokens'],client.budget.total_tokens),None),
                ('day',now.date().isoformat(),cfg['daily_requests'],cfg['daily_tokens'],cfg['daily_estimated_cny'])):
                rows = [r for r in effective_rows if r[column] == value]
                # Old accepted UNKNOWN reservations remain charged on every day.
                # Carry tokens/cost only, without duplicating today's rows or request count.
                charges = rows + ([r for r in effective_rows if r['id'] in accepted and
                                   r['actual_tokens'] is None and r['day'] != value] if column == 'day' else [])
                if len(rows) >= ncap or sum(r['accounted'] for r in charges)+reserve > tcap:
                    fail('REQUEST_OR_TOKEN_BUDGET_LIMIT')
                if ccap is not None and sum(r['cost'] if r['cost'] is not None else r['reserved_cost']
                                           for r in charges)+reserved_cost >= ccap:
                    fail('DAILY_ESTIMATED_COST_LIMIT')
            rid = uuid4().hex
            audit = {'grant_id':grant['grant_id'], 'configured_model':client.model,
                     'prices':cfg['prices'], 'time':now.isoformat(),
                     'egress':'TRUSTED_CURRENT_CONTEXT_EXACT_BYTES', 'timeout_seconds':left,
                     'local_trace_id':rid, 'phase':'RESERVED_BEFORE_SEND',
                     'usage_status':'UNKNOWN_RESERVED', 'cost_status':'UNKNOWN'}
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (rid,self.task_id,now.date().isoformat(),'INFLIGHT',reserve,reserve,None,None,
                 reserved_cost,hashlib.sha256(raw).hexdigest(),len(raw),json.dumps(audit)))
        return rid, left

    def finish(self, rid, attempt):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
            if row is None or row['task'] != self.task_id or row['status'] != 'INFLIGHT':
                fail('LEDGER_FINALIZATION_REJECTED')
            audit = json.loads(row['audit'])
            usage = attempt.get('usage', {})
            known = all(type(usage.get(k)) is int and usage[k] >= 0 for k in
                        ('input_tokens','output_tokens','total_tokens'))
            total = max(usage['total_tokens'],usage['input_tokens']+usage['output_tokens']) if known else None
            prices = audit['prices']
            hour = datetime.fromisoformat(audit['time']).hour
            # Crossing a tariff boundary uses the peak rate conservatively.
            end = datetime.now(ZoneInfo('Asia/Shanghai'))
            period = 'off_peak' if 2 <= hour < 8 and 2 <= end.hour < 8 else 'peak'
            rates = prices[period]
            cached = min(usage.get('cached_input_tokens',0),usage['input_tokens']) if known else 0
            cost = ((usage['input_tokens']-cached)*rates['input_per_million']+
                    cached*rates['cached_input_per_million']+
                    usage['output_tokens']*rates['output_per_million'])/1_000_000 if known else None
            code = attempt.get('error_code')
            if not known:
                code = code or 'UNKNOWN_USAGE'
            elif usage['total_tokens'] != usage['input_tokens']+usage['output_tokens']:
                code = code or 'USAGE_INCONSISTENT'
            elif total > row['reserved']:
                code = code or 'RESERVATION_EXCEEDED'
            if code:
                category = failure_class(code, [attempt])
                if category != 'C_MODEL_QUALITY':
                    db.execute('UPDATE control SET halted=1,reason=? WHERE id=1 AND halted=0',(code,))
                db.execute('UPDATE tasks SET stopped=1 WHERE id=?',(self.task_id,))
                audit_event(db, 'REQUEST_STOP', {'task':self.task_id, 'request':rid,
                                               'reason':code, 'category':category})
            audit.update(attempt=attempt, phase=attempt.get('phase', audit.get('phase')),
                         usage_status='PROVIDER_REPORTED' if known else 'UNKNOWN_RESERVED',
                         cost_status='VERIFIED_RATE_ESTIMATE_NOT_BILL' if known else 'UNKNOWN',
                         actual_model=attempt.get('actual_model'), estimated_cost_cny=cost)
            db.execute('UPDATE requests SET status=?,accounted=?,actual_tokens=?,cost=?,audit=? WHERE id=?',
                ('FAILED' if code else 'RECEIVED',total if known else row['reserved'],total,cost,json.dumps(audit),rid))
        if code and not attempt.get('error_code'):
            fail(code)


def enforce_request_policy(client, raw, timeout):
    policy = getattr(client, 'api_policy', None)
    if not isinstance(policy, ProjectAPIPolicy):
        fail('EGRESS_PROFILE_REQUIRED_USE_M4E43')
    try:
        return policy.before_send(client, raw, timeout)
    except ProviderError as error:
        if error.code != 'AUTOMATIC_API_DISABLED':
            policy.halt(error.code)
        raise


def complete_request_policy(client, reservation, attempt):
    if reservation is not None:
        client.api_policy.finish(reservation, attempt)


@contextmanager
def request_deadline(reservation, seconds):
    if reservation is None:
        yield
        return
    def expired(*_):
        raise TimeoutError('request wall deadline')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
