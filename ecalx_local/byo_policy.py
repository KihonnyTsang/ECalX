"""Injected request accounting for BYO only; original personal ledger is untouched."""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time
from uuid import uuid4
from . import SCIENCE_ROOT
from .configuration import read_config
from ecalx.agent.api_policy import ProjectAPIPolicy
from ecalx.agent.llm import ProviderError

def ledger_path():
    return SCIENCE_ROOT/'api_usage.sqlite3'

def usage_status():
    path=ledger_path()
    if not path.exists():return {'cost':0.,'unknown':0}
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        day=datetime.now(timezone.utc).date().isoformat()
        cost=db.execute('SELECT COALESCE(SUM(cost),0) FROM requests WHERE day=? OR known=0',(day,)).fetchone()[0]
        unknown=db.execute('SELECT COUNT(*) FROM requests WHERE known=0').fetchone()[0]
    return {'cost':cost,'unknown':unknown}

class BYOPolicy(ProjectAPIPolicy):
    def __init__(self,task_id,validator,root=None):
        self.task_id,self.validator=task_id,validator
        self.started=time.monotonic();self.path=ledger_path()

    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS requests(
            id TEXT PRIMARY KEY, task TEXT, day TEXT, known INTEGER, tokens INTEGER,
            cost REAL, payload_sha TEXT, status TEXT, prices TEXT, model TEXT)''')
        return db

    def before_send(self,client,raw,timeout):
        from ecalx.agent.config import siliconflow_settings,BASE_URL
        cfg=read_config()
        import threading,signal
        if threading.current_thread() is not threading.main_thread() or signal.getitimer(signal.ITIMER_REAL)[0]:
            raise ProviderError("请使用独立工作进程执行在线请求",code="WALL_DEADLINE_UNAVAILABLE")
        if siliconflow_settings()!=(client.model,BASE_URL) or cfg['model']!=client.model or client.budget.transport_retries:
            raise ProviderError('在线配置已改变',code='INVALID_CONFIG')
        self.validator(raw)
        reserve=len(raw)+1024+client.budget.request_tokens
        rate=max(v for v in cfg['prices'].values() if v is not None)
        cost=reserve*rate/1_000_000
        elapsed=time.monotonic()-self.started
        left=min(timeout,client.budget.request_seconds,client.budget.seconds-elapsed)
        if left<=0:raise ProviderError('任务时限已到',code='TIMEOUT')
        day=datetime.now(timezone.utc).date().isoformat();rid=uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM requests WHERE known=0').fetchone():
                raise ProviderError('存在未知用量，未自动重发',code='UNKNOWN_USAGE')
            daily=db.execute('SELECT COALESCE(SUM(cost),0) FROM requests WHERE day=?',(day,)).fetchone()[0]
            count,tokens=db.execute('SELECT COUNT(*),COALESCE(SUM(tokens),0) FROM requests WHERE task=?',(self.task_id,)).fetchone()
            if daily+cost>cfg['daily_cny'] or count>=client.budget.llm_requests or tokens+reserve>client.budget.total_tokens:
                raise ProviderError('请求前预算检查未通过',code='BUDGET_LIMIT')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?)',(rid,self.task_id,day,0,reserve,cost,
                hashlib.sha256(raw).hexdigest(),'INFLIGHT',json.dumps(cfg['prices']),client.model))
        return rid,left

    def finish(self,rid,attempt):
        usage=attempt.get('usage',{})
        known=all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens','total_tokens'))
        known=known and usage.get('input_tokens',0)+usage.get('output_tokens',0)==usage.get('total_tokens')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM requests WHERE id=? AND task=?',(rid,self.task_id)).fetchone()
            if not row or row['status']!='INFLIGHT':
                raise ProviderError('请求收据不匹配',code='LEDGER_MISMATCH')
            prices=json.loads(row['prices'])
            rate=max(v for v in prices.values() if v is not None)
            tokens=usage['total_tokens'] if known else row['tokens']
            if known and tokens>row['tokens']:known=False;tokens=max(tokens,row['tokens'])
            db.execute('UPDATE requests SET known=?,tokens=?,cost=?,status=? WHERE id=?',
                (int(known),tokens,tokens*rate/1_000_000,'FAILED' if attempt.get('error_code') or not known else 'RECEIVED',rid))

    def halt(self,reason,attempts=()):
        # Failed tasks remain immutable in the existing ResearchAgent. Unknown
        # reservations block further sends; no automatic replay or ledger reset.
        pass
