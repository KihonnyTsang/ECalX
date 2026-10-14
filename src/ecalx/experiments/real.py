"""Actual-user-measurement intake, immutable raw files, human review gate.

This service never opens the virtual DB and never starts any model training.
"""
import hashlib
import io
import json
import math
from zipfile import BadZipFile
from pathlib import Path

import pandas as pd
from ecalx.materials.chemistry import parse_formula
from .contracts import (AuthorizeUpdateInput, ExperimentError, ImportRealInput,
                        RealImportOutput, RequestModelUpdateInput, ReviewInput,
                        TrainingSelectionOutput, UpdateRequestOutput)
from .storage import Store, dump, identifier

NUMERIC = ('value', 'temperature_K', 'e_start_kV_cm', 'e_end_kV_cm', 'field_kV_cm', 'frequency_Hz', 'time_s')
TEXT = ('formula', 'batch_id', 'specimen_id', 'source', 'measurement_type', 'unit', 'direction', 'method', 'target', 'sample_form')
KINDS = {'dielectric_spectrum', 'pe_loop', 'electrocaloric'}


def normalize_rows(frame, request):
    rows, issues = [], []
    for n, raw in enumerate(frame.to_dict('records'), 2):
        row = {k: str(raw[k]).strip() or None if k in raw else None for k in TEXT}
        for key in ('formula', 'batch_id', 'specimen_id', 'source'):
            row[key] = row[key] or getattr(request, key)
        flags = []
        for key in NUMERIC:
            value = raw.get(key, '')
            try:
                row[key] = None if str(value).strip() == '' else float(value)
                if row[key] is not None and not math.isfinite(row[key]):
                    raise ValueError()
            except (ValueError, TypeError):
                row[key] = None
                flags.append('invalid_numeric:'+key)
        if str(raw.get('provenance', 'REAL_MEASUREMENT')).strip() != 'REAL_MEASUREMENT':
            flags.append('non_real_provenance_forbidden')
        for key in ('formula', 'batch_id', 'specimen_id', 'source', 'measurement_type', 'value', 'unit'):
            if row[key] is None:
                flags.append('missing:'+key)
        if row['formula']:
            try:
                parse_formula(row['formula'])
            except ValueError:
                flags.append('invalid_formula')
        kind = row['measurement_type']
        if kind not in KINDS:
            flags.append('unknown_measurement_type')
        if kind == 'electrocaloric':
            for key in ('temperature_K', 'e_start_kV_cm', 'e_end_kV_cm', 'direction', 'method', 'target', 'sample_form'):
                if row[key] is None:
                    flags.append('missing:'+key)
            if row['unit'] != 'K':
                flags.append('electrocaloric_unit_must_be_K')
            if row['method'] not in {'direct', 'indirect'}:
                flags.append('unknown_electrocaloric_method')
            if row['target'] not in {'signed_delta_T_ad_K', 'cooling_magnitude_K'}:
                flags.append('unknown_electrocaloric_target')
            if row['direction'] not in {'loading', 'unloading'}:
                flags.append('unknown_field_direction')
            a, b = row['e_start_kV_cm'], row['e_end_kV_cm']
            if a is not None and b is not None and (
                a == b or (row['direction'] == 'loading' and b < a) or (row['direction'] == 'unloading' and b > a)):
                flags.append('field_direction_conflict')
            if row['target'] == 'cooling_magnitude_K' and row['value'] is not None and row['value'] < 0:
                flags.append('negative_cooling_magnitude')
        elif kind == 'dielectric_spectrum':
            if row['temperature_K'] is None:
                flags.append('missing:temperature_K')
            if row['unit'] not in {'1', 'dimensionless'}:
                flags.append('unknown_dielectric_unit')
        elif kind == 'pe_loop':
            if row['field_kV_cm'] is None:
                flags.append('missing:field_kV_cm')
            if row['unit'] not in {'C/m2', 'uC/cm2'}:
                flags.append('unknown_polarization_unit')
        if row['temperature_K'] is not None and row['temperature_K'] <= 0:
            flags.append('nonpositive_temperature_K')
        for key in ('e_start_kV_cm', 'e_end_kV_cm', 'frequency_Hz'):
            if row[key] is not None and row[key] < 0:
                flags.append('negative:'+key)
        row.update(provenance='REAL_MEASUREMENT', original_row=int(raw.get('original_excel_row', n) or n), raw=raw, quality_issues=flags)
        rows.append(row)
        issues.extend(f'row {n}:{flag}' for flag in flags)
    if not rows:
        issues.append('no_measurement_rows')
    return rows, issues


class RealLaboratory(Store):
    def __init__(self, storage_root):
        self.raw_root = Path(storage_root)/'real/raw'
        super().__init__(Path(storage_root)/'real/experiments.sqlite3')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, metadata TEXT, raw_path TEXT,
                    raw_sha TEXT, records TEXT, issues TEXT, choice TEXT);
                CREATE TABLE IF NOT EXISTS update_requests(id TEXT PRIMARY KEY, experiment_id TEXT UNIQUE,
                    status TEXT, reviewer TEXT, note TEXT, authorization TEXT);
                CREATE TRIGGER IF NOT EXISTS freeze_submission BEFORE UPDATE ON submissions
                    BEGIN SELECT RAISE(ABORT, 'immutable actual measurement submission'); END;
            ''')

    def import_real_experiment(self, request: ImportRealInput, content: bytes | None, *, operation_key=None) -> RealImportOutput:
        with self.connect() as db:
            self.require(db, request.authorization_id, 'IMPORT_REAL', 'new')
            fingerprint = hashlib.sha256((request.model_dump_json(exclude={'authorization_id'})+hashlib.sha256(content or b'').hexdigest()).encode()).hexdigest()
            cached = self.receipt(db, operation_key, fingerprint)
            if cached is not None:
                return RealImportOutput(**cached)
            eid = ('real-op-'+hashlib.sha256(operation_key.encode()).hexdigest() if operation_key else identifier('real'))
            path, sha, records, issues = None, None, [], []
            if request.feedback_choice == 'SKIP_FEEDBACK':
                if content:
                    raise ExperimentError('SKIP_FEEDBACK不接收文件；可以继续其他流程')
            else:
                if not content:
                    raise ExperimentError('保存真实实验需要实际上传文件；不会生成演示实验记录')
                suffix = Path(request.filename).suffix.lower()
                if suffix not in {'.csv', '.xlsx'} or len(content) > 20*1024*1024:
                    raise ExperimentError('仅接受20MB以内CSV/XLSX')
                # The unique server ID is the directory; never trust an upload path.
                self.raw_root.mkdir(parents=True, exist_ok=True)
                raw_dir = self.raw_root/eid
                raw_dir.mkdir(exist_ok=operation_key is not None)
                path = raw_dir/('original'+suffix)
                if path.exists():
                    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(content).digest():
                        raise ExperimentError('幂等原件内容不符；拒绝覆盖')
                else:
                    with path.open('xb') as handle:
                        handle.write(content)
                sha = hashlib.sha256(content).hexdigest()
                try:
                    if suffix == '.csv':
                        frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding='utf-8-sig')
                    else:
                        # Read ALL sheets, keep original rows/sheet names; no silent loss.
                        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=str, keep_default_na=False)
                        frame = pd.concat([v.assign(original_sheet=k, original_excel_row=range(2, len(v)+2)) for k, v in sheets.items()], ignore_index=True)
                    records, issues = normalize_rows(frame.fillna(''), request)
                except (ValueError, UnicodeError, OSError, ImportError, KeyError, BadZipFile) as error:
                    issues = ['parse_failed:'+type(error).__name__, 'no_measurement_rows']
                if any('non_real_provenance_forbidden' in issue for issue in issues):
                    # Never put declared virtual measurements in the real DB.
                    path.unlink()
                    raw_dir.rmdir()
                    raise ExperimentError('SIMULATED/HISTORICAL_REPLAY不能进入真实测量存储')
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?)',
                       (eid, request.model_dump_json(exclude={'authorization_id'}), str(path) if path else None,
                        sha, dump(records), dump(issues), request.feedback_choice))
            rid = None
            if request.feedback_choice == 'REQUEST_MODEL_UPDATE':
                rid = self._request(db, eid)
            self.event(db, 'IMPORT_REAL' if content else 'SKIP_FEEDBACK', eid,
                       {'feedback_choice': request.feedback_choice, 'raw_sha256': sha,
                        'row_count': len(records), 'quality_issues': issues, 'request_id': rid,
                        'grant': request.authorization_id, 'training_started': False})
            output = RealImportOutput(experiment_id=eid, feedback_choice=request.feedback_choice,
                                    raw_sha256=sha, row_count=len(records), quality_issues=issues, request_id=rid)
            self.save_receipt(db, operation_key, fingerprint, output.model_dump())
            return output

    def _request(self, db, eid):
        rid = identifier('update')
        db.execute('INSERT INTO update_requests VALUES(?,?,?,NULL,NULL,NULL)', (rid, eid, 'PENDING_REVIEW'))
        self.event(db, 'REQUEST_MODEL_UPDATE', eid, {'request_id': rid, 'status': 'PENDING_REVIEW', 'training_started': False})
        return rid

    def request_model_update(self, request: RequestModelUpdateInput) -> UpdateRequestOutput:
        with self.connect() as db:
            self.require(db, request.authorization_id, 'REQUEST_MODEL_UPDATE', request.experiment_id)
            row = self._submission(db, request.experiment_id)
            if row['choice'] != 'REQUEST_MODEL_UPDATE':
                raise ExperimentError('SAVE_ONLY/SKIP_FEEDBACK不可转入训练池；需另行明确授权的新提交')
            r = db.execute('SELECT * FROM update_requests WHERE experiment_id=?', (request.experiment_id,)).fetchone()
            return UpdateRequestOutput(request_id=r['id'], status=r['status'], experiment_id=request.experiment_id)

    def _submission(self, db, eid):
        row = db.execute('SELECT * FROM submissions WHERE id=?', (eid,)).fetchone()
        if row is None:
            raise ExperimentError('未知真实实验ID')
        return row

    def _request_row(self, db, rid):
        row = db.execute('SELECT * FROM update_requests WHERE id=?', (rid,)).fetchone()
        if row is None:
            raise ExperimentError('未知训练申请ID')
        return row

    def _eligible(self, db, eid):
        row = self._submission(db, eid)
        if row['choice'] != 'REQUEST_MODEL_UPDATE' or json.loads(row['issues']):
            raise ExperimentError('数据未通过质量校验或未申请模型更新')
        path = Path(row['raw_path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['raw_sha']:
            raise ExperimentError('真实原件缺失或SHA不符；拒绝训练准入')
        measurements = [r for r in json.loads(row['records']) if r['measurement_type'] == 'electrocaloric'
                        and r['provenance'] == 'REAL_MEASUREMENT' and not r['quality_issues']]
        if not measurements:
            raise ExperimentError('没有完整电卡标签；曲线仅保存分析，不自动推导训练标签')
        return [dict(r, experiment_id=eid, raw_sha256=row['raw_sha']) for r in measurements]

    def review(self, request: ReviewInput) -> UpdateRequestOutput:
        with self.connect() as db:
            grant = self.require(db, request.authorization_id, 'REVIEW_REAL', request.request_id)
            r = self._request_row(db, request.request_id)
            if request.reviewer.strip() != grant['actor']:
                raise ExperimentError('审核者必须与授权操作者一致')
            if r['status'] != 'PENDING_REVIEW':
                raise ExperimentError('该申请已审核；不会覆盖旧审核记录')
            if request.decision == 'APPROVE':
                self._eligible(db, r['experiment_id'])
            status = 'REVIEWED' if request.decision == 'APPROVE' else 'REJECTED'
            db.execute('UPDATE update_requests SET status=?, reviewer=?, note=? WHERE id=?',
                       (status, request.reviewer, request.note, request.request_id))
            self.event(db, 'HUMAN_REVIEW', r['experiment_id'], request.model_dump(exclude={'authorization_id'}) | {'status': status})
            return UpdateRequestOutput(request_id=r['id'], status=status, experiment_id=r['experiment_id'])

    def authorize_model_update(self, request: AuthorizeUpdateInput) -> UpdateRequestOutput:
        with self.connect() as db:
            self.require(db, request.authorization_id, 'AUTHORIZE_REAL_UPDATE', request.request_id)
            r = self._request_row(db, request.request_id)
            if r['status'] != 'REVIEWED':
                raise ExperimentError('需先通过数据校验与人工审核')
            self._eligible(db, r['experiment_id'])
            db.execute('UPDATE update_requests SET status=?,authorization=? WHERE id=?',
                       ('AUTHORIZED', request.authorization_id, r['id']))
            self.event(db, 'AUTHORIZE_TRAINING_ADMISSION', r['experiment_id'],
                       {'request_id': r['id'], 'grant': request.authorization_id, 'training_started': False,
                        'note': 'M4-E1只批准准入；真实模型训练/独立测试/升级留待后续阶段'})
            return UpdateRequestOutput(request_id=r['id'], status='AUTHORIZED', experiment_id=r['experiment_id'])

    def select_training_data(self) -> TrainingSelectionOutput:
        with self.connect() as db:
            result = []
            for r in db.execute('SELECT experiment_id FROM update_requests WHERE status=?', ('AUTHORIZED',)):
                result.extend(self._eligible(db, r['experiment_id']))
            return TrainingSelectionOutput(records=result)

    def list_experiments(self):
        with self.connect() as db:
            results = []
            for row in db.execute('SELECT * FROM submissions ORDER BY rowid DESC'):
                r = db.execute('SELECT * FROM update_requests WHERE experiment_id=?', (row['id'],)).fetchone()
                results.append({'experiment_id': row['id'], 'provenance': 'REAL_MEASUREMENT',
                                'feedback_choice': row['choice'], 'metadata': json.loads(row['metadata']),
                                'raw_path': row['raw_path'], 'raw_sha256': row['raw_sha'],
                                'records': json.loads(row['records']), 'quality_issues': json.loads(row['issues']),
                                'update_request': dict(r) if r else None})
            return results

    def raw_bytes(self, eid):
        with self.connect() as db:
            row = self._submission(db, eid)
            if not row['raw_path']:
                raise ExperimentError('跳过反馈未保存测量文件')
            data = Path(row['raw_path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != row['raw_sha']:
                raise ExperimentError('真实原文件SHA不符')
            return data
