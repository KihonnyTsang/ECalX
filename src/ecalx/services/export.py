import csv
import io
import json

from ecalx import __version__


def report_json(report):
    return json.dumps({"schema_version": "1", "app_version": __version__, **report.to_dict()},
                      ensure_ascii=False, indent=2, allow_nan=False)


def safe_csv_cell(value):
    # Prevent imported formulas/text from executing in spreadsheet software.
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def report_csv(report):
    output = io.StringIO()
    fields = ["candidate_id", "formula", "status", "ranking_context", "material_family", "sample_form",
              "delta_T_ad_K", "temperature_K", "e_start_kV_cm", "e_end_kV_cm", "field_path",
              "measurement_method", "source_id", "doi", "url", "original_row", "quality_flags",
              "e_reported_kV_cm", "e_reported_kind", "figure_id", "composition_key", "delta_t_basis", "delta_t_sign_convention",
              "import_id", "predictions", "model_state", "model_version", "data_version"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for candidate in report.candidates:
        merged = {**candidate["evidence"], **candidate, "model_state": report.model_state,
                  "predictions": json.dumps(candidate["predictions"]),
                  "quality_flags": json.dumps(candidate["evidence"]["quality_flags"])}
        writer.writerow({k: (merged.get(k) if isinstance(merged.get(k), (int, float)) else safe_csv_cell(merged.get(k))) for k in fields})
    return "\ufeff" + output.getvalue()


def evidence_csv(records):
    """Browse export, explicitly distinct from scientifically matched candidates."""
    from ecalx.data.importers import CSV_COLUMNS
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["evidence_status", *CSV_COLUMNS, "import_id"])
    writer.writeheader()
    for record in records:
        row = {"evidence_status": "protocol_complete" if record.recommendable else "evidence_only_incomplete_protocol",
               **record.to_dict()}
        writer.writerow({k: v if isinstance(v, (int, float)) else safe_csv_cell(
            json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in row.items()})
    return "\ufeff" + output.getvalue()
