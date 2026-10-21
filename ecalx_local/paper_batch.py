"""Local batch receipts and reversible paper imports in EvidenceRepository.

Unresolved/citation evidence lives in the existing sources table, never records.
No prediction, external API, migration, or overwrite of existing science records.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from ecalx.data.repository import EvidenceRepository
from . import PERSONAL
from .import_data import commit
from .paper_extract import extract_pdf, retain_local_draft, prepare_review, EXTRACTOR_VERSION
from .paper_policy import assess, auto_gate, POLICY_VERSION

MAX_BATCH_PAPERS=200
MAX_BATCH_BYTES=500_000_000


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def process_batch(files,workspace):
    """files is an iterable of (filename, bytes). Failures are isolated per PDF."""
    if PERSONAL:raise ValueError('仅在 BYO 工作区处理论文')
    jobs=[]; seen=set();total_bytes=0
    for index,(filename,content) in enumerate(files):
        if index>=MAX_BATCH_PAPERS:raise ValueError('每批最多 200 篇')
        total_bytes+=len(content)
        if total_bytes>MAX_BATCH_BYTES:raise ValueError('每批总计最多 500 MB')
        sha=hashlib.sha256(content).hexdigest()
        if sha in seen:
            jobs.append({'filename':filename,'duplicate_pdf':sha});continue
        seen.add(sha)
        try:
            draft=extract_pdf(content,filename)
            retained=retain_local_draft(content,draft,workspace)
            jobs.append({'draft':draft,'content':content,'retained':retained})
        except (ValueError,OSError) as error:jobs.append({'filename':filename,'error':str(error)})
    persist_batch(jobs,workspace)
    return jobs


def persist_batch(jobs,workspace):
    """Immutable evidence revisions, including cited values without a false DOI."""
    if PERSONAL:raise ValueError('仅限 BYO 工作区')
    repo=EvidenceRepository(Path(workspace)/'data/evidence.sqlite3')
    with closing(repo._connect()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        for job in jobs:
            if 'draft' not in job:continue
            draft=job['draft']
            if hashlib.sha256(job['content']).hexdigest()!=draft.sha256:raise ValueError('PDF 已变化')
            revision=_digest([EXTRACTOR_VERSION,POLICY_VERSION,draft.sha256,draft.candidates])
            for row in draft.candidates:
                # sources contains evidence only. No cited measurement is an
                # EvidenceRecord, and the enclosing DOI has a distinct role.
                payload={'kind':'PAPER_EXTRACTION_EVIDENCE','pdf_sha256':draft.sha256,
                    'filename':draft.filename,'enclosing_paper_doi':draft.doi,
                    'original_measurement_doi':row['attribution']['original_measurement_doi'],
                    'extractor_version':EXTRACTOR_VERSION,'policy_version':POLICY_VERSION,
                    'revision':revision,'candidate':row,'local_only':True}
                sid='paper-evidence-'+draft.sha256+'-'+row['candidate_id']
                db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)',
                    (sid,'paper-draft-'+revision,json.dumps(payload,ensure_ascii=False,allow_nan=False)))


def summarize(jobs,workspace=None):
    counts={'papers_processed':0,'failed_papers':0,'duplicate_papers':0,'auto_eligible':0,
            'auto_imported':0,'needs_review':0,'cited_records':0,'theory_records':0,
            'duplicate_records':0,'unattended_enabled':auto_gate()['enabled']}
    prior={}
    if workspace:
        prior={r.record_id:r for r in EvidenceRepository(Path(workspace)/'data/evidence.sqlite3').records()}
    # Exact duplicate semantic points in a PDF are surfaced, not silently merged.
    seen=set()
    imported_keys={(r.raw_metadata_json.get('paper',{}).get('sha256'),
                    r.raw_metadata_json.get('paper_candidate_id')) for r in prior.values()}
    for job in jobs:
        if 'error' in job:counts['failed_papers']+=1;continue
        if 'duplicate_pdf' in job:counts['duplicate_papers']+=1;continue
        draft=job['draft'];counts['papers_processed']+=1
        for row in draft.candidates:
            route=row['admission']['route'];kind=row['attribution']['kind']
            v=row['values']
            # Empty evidence-only pages and unknown-condition points are not
            # duplicate measurements merely because their missing values match.
            complete=(any(v.get(k) is not None for k in ('delta_t','delta_s')) and
                all(v.get(k) not in (None,'','unknown') for k in ('formula','temperature','e_delta','method')) and
                bool(v.get('specimen_id') or v.get('sample_form')))
            key=(draft.sha256,_digest(v)) if complete else None
            if (key is not None and key in seen) or (draft.sha256,row['candidate_id']) in imported_keys:
                counts['duplicate_records']+=1
            if key is not None:seen.add(key)
            if route=='CITATION':counts['cited_records']+=1
            elif route=='AUTO_ELIGIBLE':counts['auto_eligible']+=1
            else:counts['needs_review']+=1
            if kind=='THEORY_SIMULATION':counts['theory_records']+=1
    return counts


def prepare_automatic(content,draft,*,license_note):
    if not auto_gate()['enabled']:raise ValueError(auto_gate()['reason'])
    # Reparse the original bytes: mutable UI dictionaries cannot bypass rules.
    fresh=extract_pdf(content,draft.filename)
    if fresh.sha256!=draft.sha256:raise ValueError('PDF 已变化')
    selected=[{'selected':True,'candidate_id':r['candidate_id'],'values':r['values']}
              for r in fresh.candidates if r['admission']['route']=='AUTO_ELIGIBLE']
    if not selected:return None
    prepared=prepare_review(content,fresh,selected,confirmed=True,license_note=license_note)
    for r in prepared['records']:
        r.raw_metadata_json.update(entry_method='PAPER_RULES_AUTO',provenance='RULE_VERIFIED_EXPERIMENT',
            admission_gate=auto_gate())
    return prepared


def import_automatic(jobs,workspace,*,license_note):
    # Gate is checked before even creating a database.
    if not auto_gate()['enabled']:raise ValueError(auto_gate()['reason'])
    logs=[]
    for job in jobs:
        if 'draft' not in job:continue
        p=prepare_automatic(job['content'],job['draft'],license_note=license_note)
        if p is not None:logs.append(commit(job['content'],p,workspace))
    return logs


def revoke_paper_import(workspace,import_id,reason):
    """Delete only rows still identical to this import's snapshot, retain an audit.

    Old scientific data and later edits are never touched. An idempotent tombstone
    prevents the same import from silently resurrecting revoked data.
    """
    if PERSONAL:raise ValueError('仅限 BYO 工作区')
    if not reason.strip():raise ValueError('请填写撤销原因')
    repo=EvidenceRepository(Path(workspace)/'data/evidence.sqlite3')
    with closing(repo._connect()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        found=db.execute('SELECT payload FROM imports WHERE import_id=?',(import_id,)).fetchone()
        if not found:raise ValueError('导入不存在')
        log=json.loads(found[0])
        if log.get('revoked'):return log
        snapshot=log.get('paper_record_snapshots')
        if not snapshot:raise ValueError('只支持带完整快照的本轮论文导入；历史科研数据不可撤销')
        rows=dict(db.execute('SELECT record_id,payload FROM records'))
        for rid,expected in snapshot.items():
            if rid in rows and _digest(json.loads(rows[rid]))!=expected:
                raise ValueError('记录已被后续修改，撤销中止：'+rid)
        before=repo.data_version
        removed=[]; removed_records={}
        for rid in snapshot:
            if rid in rows:
                removed_records[rid]=json.loads(rows[rid])
                db.execute('DELETE FROM records WHERE record_id=?',(rid,));removed.append(rid)
        current=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records ORDER BY record_id')]
        after='sha256:'+_digest(current)
        log.update(revoked=True,revocation={'reason':reason,'removed_ids':removed,'removed_records':removed_records,'before_version':before,
            'after_version':after,'timestamp_utc':datetime.now(timezone.utc).isoformat()})
        db.execute('UPDATE imports SET payload=? WHERE import_id=?',(json.dumps(log,ensure_ascii=False),import_id))
    return log
