"""Local trusted UI capability boundary; not a multi-user authentication service."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ExperimentError

VERSION = 'm4e2-1'


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def identifier(prefix):
    return prefix + '-' + uuid.uuid4().hex


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operation_receipts(key TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY, action TEXT, subject TEXT, actor TEXT);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, at TEXT, action TEXT, subject TEXT, detail TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, action, subject, detail):
        db.execute('INSERT INTO audit(at,action,subject,detail) VALUES(?,?,?,?)',
                   (datetime.now(timezone.utc).isoformat(), action, subject, dump(detail)))

    def authorize(self, action, subject, actor, *, confirmed=False):
        # Only the trusted human-facing UI/CLI calls this. Future Agent tool registry
        # MUST NOT expose grant issuance; tokens are scoped to a service DB/action/run.
        if confirmed is not True or not actor.strip() or not subject:
            raise ExperimentError('需要明确人工确认与操作者；Agent不能自行签发授权')
        token = identifier('grant')
        with self.connect() as db:
            db.execute('INSERT INTO grants VALUES(?,?,?,?)', (token, action, subject, actor))
            self.event(db, 'AUTHORIZE', subject, {'action': action, 'actor': actor, 'grant': token})
        return token

    def require(self, db, token, action, subject):
        grant = db.execute('SELECT * FROM grants WHERE id=? AND action=? AND subject=?',
                           (token, action, subject)).fetchone()
        if grant is None:
            raise ExperimentError('缺少有效、操作与任务匹配的显式授权')
        return dict(grant)

    def receipt(self, db, key, fingerprint):
        if key is None:
            return None
        row = db.execute('SELECT * FROM operation_receipts WHERE key=?', (key,)).fetchone()
        if row and row['fingerprint'] != fingerprint:
            raise ExperimentError('幂等操作ID不能改变参数或文件')
        return json.loads(row['payload']) if row else None

    def save_receipt(self, db, key, fingerprint, output):
        if key is not None:
            db.execute('INSERT INTO operation_receipts VALUES(?,?,?)', (key, fingerprint, dump(output)))

    def audit(self):
        with self.connect() as db:
            return [dict(r) | {'detail': json.loads(r['detail'])} for r in db.execute('SELECT * FROM audit ORDER BY seq')]
