import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ecalx.data.importers import parse_csv
from ecalx.data.schema import EvidenceRecord
from ecalx.data.quality import flag_possible_duplicates
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
            flag_possible_duplicates([r for r in records if r.record_id not in existing],
                                     [EvidenceRecord(**json.loads(p)) for p in existing.values()])
            duplicates, inserted = [], 0
            for record in records:
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
