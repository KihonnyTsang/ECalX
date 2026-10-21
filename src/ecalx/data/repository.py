import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ecalx.data.importers import parse_csv
from ecalx.data.schema import EvidenceRecord
from ecalx.data.quality import flag_possible_duplicates, source_cell_identity, same_source_observation
from ecalx.data.schema import EXPERIMENTAL_RESPONSES
from ecalx.domain.constraints import ValidationError


class EvidenceRepository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS records (record_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (source_id TEXT, import_id TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY (source_id, import_id));
                CREATE TABLE IF NOT EXISTS imports (import_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                    payload TEXT NOT NULL);
            """)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def records(self):
        with closing(self._connect()) as connection:
            return [EvidenceRecord(**json.loads(row[0])).validate() for row in connection.execute("SELECT payload FROM records ORDER BY record_id")]

    def logs(self):
        with closing(self._connect()) as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT payload FROM imports ORDER BY rowid DESC")]

    def query(self, *, response=None, value_min=None, value_max=None, text='', exclude_elements=(),
              specimen_id=None, field_path=None, reported_field_kV_cm=None):
        """Query typed observations without promoting them into performance candidates."""
        from ecalx.materials.formulas import formula_elements
        if response is not None and response not in EXPERIMENTAL_RESPONSES:
            raise ValidationError('未知实验响应字段')
        if (value_min is not None or value_max is not None) and response is None:
            raise ValidationError('数值筛选需要指定实验响应')
        from ecalx.parsing.units import finite_number
        if value_min is not None:value_min=finite_number(value_min,'响应下限')
        if value_max is not None:value_max=finite_number(value_max,'响应上限')
        if value_min is not None and value_max is not None and value_min > value_max:
            raise ValidationError('响应下限不能超过上限')
        if reported_field_kV_cm is not None:reported_field_kV_cm=finite_number(reported_field_kV_cm,'源报告场')
        result = []
        for r in self.records():
            value = getattr(r, response) if response else None
            if response and (value is None or (value_min is not None and value < value_min)
                             or (value_max is not None and value > value_max)):
                continue
            if specimen_id and r.specimen_id != specimen_id:
                continue
            if field_path and r.field_path != field_path:
                continue
            if reported_field_kV_cm is not None and r.e_reported_kV_cm != reported_field_kV_cm:
                continue
            if exclude_elements:
                elements = formula_elements(r.formula_normalized or r.formula_raw)
                if elements is None or set(exclude_elements) & elements:
                    continue
            if text.casefold() not in json.dumps(r.to_dict(), ensure_ascii=False).casefold():
                continue
            result.append(r)
        return result

    @property
    def data_version(self):
        payload = json.dumps([r.to_dict() for r in self.records()], sort_keys=True, ensure_ascii=False)
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    def import_csv(self, content, filename, origin, license_note, mapping=None):
        if not origin.strip() or not license_note.strip():
            raise ValidationError("请填写原始文件来源和已核实的许可/使用条件；系统不会代替许可审核")
        records = parse_csv(content, mapping)
        digest = hashlib.sha256(content).hexdigest()
        # Same bytes with a different mapping is a different import, never silent reinterpretation.
        identity = json.dumps([digest, mapping or {}, origin, license_note], sort_keys=True)
        import_id = hashlib.sha256(identity.encode()).hexdigest()
        raw_dir = self.path.parent.parent / "raw"
        raw_dir.mkdir(exist_ok=True)
        raw_path = raw_dir / (digest + ".csv")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            found = connection.execute("SELECT payload FROM imports WHERE import_id=?", (import_id,)).fetchone()
            if found:
                return {**json.loads(found[0]), "already_imported": True}
            existing = dict(connection.execute("SELECT record_id, payload FROM records"))
            locations = {source_cell_identity(r): r for r in
                         (EvidenceRecord(**json.loads(p)) for p in existing.values()) if source_cell_identity(r)}
            flag_possible_duplicates([r for r in records if r.record_id not in existing],
                                     [EvidenceRecord(**json.loads(p)) for p in existing.values()])
            duplicates, inserted = [], 0
            for record in records:
                location = source_cell_identity(record)
                if location and location in locations:
                    if not same_source_observation(locations[location], record):
                        raise ValidationError('同一来源单元格数值或物理口径冲突，未覆盖：' + record.record_id)
                    duplicates.append(record.record_id)
                    continue
                if record.record_id in existing:
                    prior = json.loads(existing[record.record_id])
                    comparable = record.to_dict()
                    prior.pop("import_id", None)
                    comparable.pop("import_id", None)
                    if prior != comparable:
                        raise ValidationError("record_id 已存在且内容不同，未覆盖：" + record.record_id)
                    duplicates.append(record.record_id)
                    continue
                record.import_id = import_id
                connection.execute("INSERT INTO records VALUES (?, ?)", (record.record_id, json.dumps(record.to_dict(), ensure_ascii=False)))
                inserted += 1
                if location:
                    locations[location] = record
                if record.source_id:
                    connection.execute("INSERT OR IGNORE INTO sources VALUES (?, ?, ?)",
                                       (record.source_id, import_id, json.dumps({"origin": origin, "license_note": license_note})))
            log = {"import_id": import_id, "sha256": digest, "filename": Path(filename).name,
                   "origin": origin, "license_note": license_note, "mapping": mapping or "canonical_headers",
                   "timestamp_utc": datetime.now(timezone.utc).isoformat(), "inserted": inserted,
                   "duplicate_ids_skipped": duplicates, "raw_file": str(raw_path),
                   "quality_flags": {r.record_id: r.quality_flags for r in records if r.quality_flags}}
            # Exclusive creation preserves original bytes; hash-based path avoids uploaded path traversal.
            if raw_path.exists():
                if hashlib.sha256(raw_path.read_bytes()).hexdigest() != digest:
                    raise ValidationError("原始文件校验失败，拒绝覆盖")
            else:
                with raw_path.open("xb") as handle:
                    handle.write(content)
            connection.execute("INSERT INTO imports VALUES (?, ?, ?)", (import_id, digest, json.dumps(log, ensure_ascii=False)))
        return log
