"""CSV adapter: canonical field -> source column mapping; never assumes source semantics."""
import csv
import io
import json
from dataclasses import fields

from ecalx.data.schema import EvidenceRecord
from ecalx.data.quality import flag_possible_duplicates
from ecalx.domain.constraints import ValidationError
from ecalx.parsing.units import field_kV_cm, temperature_K

NUMERIC = {"temperature_K", "e_start_kV_cm", "e_end_kV_cm", "delta_T_ad_K",
           "e_reported_kV_cm",
           "delta_S_J_kgK", "Tc_K", "Cp_J_kgK", "density_kg_m3"}
EXTRA = {"temperature_value", "temperature_unit", "e_start_value", "e_end_value", "field_unit"}
JSON_FIELDS = {"processing_json", "raw_metadata_json", "quality_flags"}
CSV_COLUMNS = [f.name for f in fields(EvidenceRecord) if f.name != "import_id"]


def csv_template():
    # Header only: deliberately no invented measurements.
    return ",".join(CSV_COLUMNS) + "\n"


def parse_csv(content: bytes, mapping=None):
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        headers = reader.fieldnames or []
        if len(set(headers)) != len(headers):
            raise ValidationError("CSV 列名不能重复")
        if mapping is None:
            mapping = {}
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ValidationError("字段映射必须是 JSON 对象：规范字段名 → CSV 列名")
        mapping = mapping or {name: name for name in headers if name in set(CSV_COLUMNS) | EXTRA}
        if set(mapping) - (set(CSV_COLUMNS) | EXTRA):
            raise ValidationError("字段映射包含未知规范字段")
        if set(mapping.values()) - set(headers):
            raise ValidationError("字段映射引用了不存在的 CSV 列")
        for name in ("record_id", "formula_raw"):
            if name not in mapping:
                raise ValidationError("缺少必要列：" + name)
        if "temperature_value" in mapping and "temperature_K" in mapping:
            raise ValidationError("temperature_value 与 temperature_K 只能映射一个，避免单位歧义")
        for suffix in ("start", "end"):
            if f"e_{suffix}_value" in mapping and f"e_{suffix}_kV_cm" in mapping:
                raise ValidationError("电场原单位与规范单位字段不能重复映射")
        records, ids = [], set()
        for row_number, row in enumerate(reader, 2):
            if None in row:
                raise ValidationError(f"CSV 第 {row_number} 行列数超出表头；包含逗号的 JSON 需加引号")
            values = {name: (row[column] or "").strip() for name, column in mapping.items()}
            values = {name: value for name, value in values.items() if value != ""}
            for name in JSON_FIELDS & values.keys():
                values[name] = json.loads(values[name])
            if "temperature_value" in values:
                if "temperature_unit" not in values:
                    raise ValidationError("temperature_value 必须同时给出 temperature_unit")
                values["temperature_K"] = temperature_K(values.pop("temperature_value"), values["temperature_unit"])
            for suffix in ("start", "end"):
                if f"e_{suffix}_value" in values:
                    if "field_unit" not in values:
                        raise ValidationError("电场原始值必须同时给出 field_unit")
                    values[f"e_{suffix}_kV_cm"] = field_kV_cm(values.pop(f"e_{suffix}_value"), values["field_unit"])
            values = {k: v for k, v in values.items() if k not in EXTRA}
            values.setdefault("original_row", str(row_number))
            metadata = values.setdefault("raw_metadata_json", {})
            if not isinstance(metadata, dict):
                raise ValidationError("raw_metadata_json 必须为对象")
            metadata["csv_original_row"] = dict(row)
            record = EvidenceRecord(**values).validate()
            if record.record_id in ids:
                raise ValidationError("文件内重复 record_id：" + record.record_id)
            ids.add(record.record_id)
            records.append(record)
        if not records:
            raise ValidationError("CSV 只有表头或为空，没有可导入记录")
        flag_possible_duplicates(records)
        return records
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, csv.Error) as error:
        raise ValidationError("CSV 格式错误：" + str(error)) from None
