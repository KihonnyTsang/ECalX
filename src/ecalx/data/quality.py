import json


def measurement_fingerprint(record):
    """Warn only: matching numeric points do not prove duplicate physical specimens."""
    names = ("source_id", "formula_raw", "formula_normalized", "material_id", "specimen_id",
             "material_family", "sample_form", "processing_json", "temperature_K",
             "e_start_kV_cm", "e_end_kV_cm", "e_reported_kV_cm", "e_reported_kind", "figure_id",
             "field_path", "measurement_method", "delta_T_ad_K", "delta_t_sign_convention")
    return json.dumps({name: getattr(record, name) for name in names}, sort_keys=True, ensure_ascii=False)


def flag_possible_duplicates(records, existing=()):
    seen = {measurement_fingerprint(r) for r in existing}
    for record in records:
        fingerprint = measurement_fingerprint(record)
        if fingerprint in seen:
            record.quality_flags = sorted(set(record.quality_flags + ["possible_duplicate_measurement"]))
        seen.add(fingerprint)
