"""Offline host-only evidence review and append-only usage adjustments. No credentials/network."""
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationError, model_validator
from ecalx.experiments.contracts import Contract
from .api_policy import ROOT, ProjectAPIPolicy, audit_event, fail, read_json
from .config import MODEL

SCHEMA = '''
CREATE TABLE IF NOT EXISTS usage_adjustments(
 request_id TEXT PRIMARY KEY, receipt_sha256 TEXT NOT NULL,
 provider_request_id TEXT UNIQUE NOT NULL, evidence_sha256 TEXT UNIQUE NOT NULL,
 evidence TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL, time TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS usage_adjustments_no_update BEFORE UPDATE ON usage_adjustments
BEGIN SELECT RAISE(ABORT,'usage adjustments are append only'); END;
CREATE TRIGGER IF NOT EXISTS usage_adjustments_no_delete BEFORE DELETE ON usage_adjustments
BEGIN SELECT RAISE(ABORT,'usage adjustments are append only'); END;
'''
HEX = r'^[a-f0-9]{64}$'
ID = r'^[A-Za-z0-9_.:-]{1,160}$'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Evidence(Contract):
    version: Literal[1]
    provider: Literal['siliconflow']
    kind: Literal['REQUEST_TOKENS', 'REQUEST_AMOUNT', 'ACCOUNT_AGGREGATE', 'ESTIMATE']
    source_kind: Literal['PROVIDER_BILL', 'PROVIDER_CONFIRMATION']
    source_url: str = Field(max_length=500)
    source_reference: str = Field(min_length=1, max_length=500)
    artifact_sha256: str = Field(pattern=HEX)
    account_alias: str = Field(pattern=ID)  # Masked local alias; no login/account credentials.
    obtained_at: str
    period_start: str
    period_end: str
    request_id: str = Field(pattern=ID)
    receipt_sha256: str = Field(pattern=HEX)
    provider_request_id: str | None = Field(default=None, pattern=ID)
    model: str | None = None
    request_time: str | None = None
    identity_basis: Literal['TRACE_MATCH', 'PROVIDER_CONFIRMED_MAPPING'] | None = None
    identity_reference: str | None = Field(default=None, min_length=1, max_length=1000)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    billed_cny: float | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def valid(self):
        u = urlparse(self.source_url)
        if u.scheme != 'https' or u.hostname not in {'cloud.siliconflow.cn', 'docs.siliconflow.cn', 'siliconflow.cn', 'www.siliconflow.cn'} or u.username or u.password or u.query or u.fragment:
            raise ValueError('Official source URL without credentials/query required')
        times = [datetime.fromisoformat(getattr(self, k)) for k in
                 ('period_start', 'period_end', 'obtained_at')]
        if any(t.tzinfo is None for t in times) or not times[0] <= times[1] <= times[2]:
            raise ValueError('Timezone-aware evidence dates must cover request')
        if self.billed_cny is not None and not math.isfinite(self.billed_cny):
            raise ValueError('Invalid amount')
        if self.request_time is not None:
            t = datetime.fromisoformat(self.request_time)
            if t.tzinfo is None or not times[0] <= t <= times[1]:
                raise ValueError('Request outside evidence period')
        if self.kind == 'REQUEST_TOKENS':
            if any(v is None for v in (self.provider_request_id, self.model, self.request_time, self.identity_basis, self.identity_reference)):
                raise ValueError('Request identity required')
            if any(type(v) is not int for v in (self.input_tokens, self.output_tokens, self.total_tokens)) or self.input_tokens + self.output_tokens != self.total_tokens:
                raise ValueError('Exact consistent request usage required')
        elif any(v is not None for v in (self.input_tokens, self.output_tokens, self.total_tokens)):
            raise ValueError('Amount/aggregate/estimate cannot assert exact request tokens')
        return self


def validate_evidence(value, row):
    try:
        e = Evidence.model_validate(value)
    except (ValueError, ValidationError):
        fail('RECONCILIATION_EVIDENCE_INVALID')  # Never echo private input/validation errors.
    if e.request_id != row['id'] or e.receipt_sha256 != digest(dict(row)):
        fail('RECONCILIATION_RECEIPT_MISMATCH')
    if row['actual_tokens'] is not None or row['status'] == 'INFLIGHT':
        fail('RECONCILIATION_REQUEST_NOT_ELIGIBLE')
    from zoneinfo import ZoneInfo
    start, end = (datetime.fromisoformat(v).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() for v in (e.period_start, e.period_end))
    if not start <= row['day'] <= end:
        fail('RECONCILIATION_DATE_MISMATCH')
    if e.kind != 'REQUEST_TOKENS':
        return e
    a = json.loads(row['audit'])
    model = a.get('configured_model', MODEL if a.get('mode') == 'LEGACY_REAL_REQUEST' else None)
    t = datetime.fromisoformat(e.request_time)
    if e.model != model or t.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() != row['day']:
        fail('RECONCILIATION_MODEL_OR_DATE_MISMATCH')
    if e.identity_basis == 'TRACE_MATCH':
        if a.get('local_trace_id') != e.provider_request_id or not a.get('time') or datetime.fromisoformat(a['time']) != t:
            fail('RECONCILIATION_TRACE_OR_TIME_MISMATCH')
    elif e.source_kind != 'PROVIDER_CONFIRMATION':
        fail('RECONCILIATION_SUPPLIER_MAPPING_REQUIRED')
    # Missing legacy timestamps/trace IDs are NOT invented. A supplier must explicitly
    # map this exact receipt/task/attempt to its unique request and timestamp.
    return e


def adjustments(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='usage_adjustments'").fetchone():
        return []
    return [dict(r) for r in db.execute('SELECT * FROM usage_adjustments ORDER BY request_id')]


def effective_usage(db, rows):
    """Overlay for admission/reporting only. Original receipts/reservations never change."""
    result = {r['id']:dict(r) for r in rows}
    for adj in adjustments(db):
        row = result.get(adj['request_id'])
        if row is None:
            fail('LEDGER_ADJUSTMENT_ORPHAN')
        e = validate_evidence(json.loads(adj['evidence']), row)
        if (e.kind != 'REQUEST_TOKENS' or adj['receipt_sha256'] != e.receipt_sha256 or
                adj['provider_request_id'] != e.provider_request_id or digest(e.model_dump()) != adj['evidence_sha256']):
            fail('LEDGER_ADJUSTMENT_INVALID')
        # Keep at least the full original reservation even after exact reconciliation.
        row.update(actual_tokens=e.total_tokens, accounted=max(row['accounted'], e.total_tokens),
                   cost=max(row['reserved_cost'], e.billed_cny or 0), reconciled=True)
    return list(result.values())


