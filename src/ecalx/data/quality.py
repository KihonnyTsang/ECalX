import json


def measurement_fingerprint(record):
    """Warn only: matching numeric points do not prove duplicate physical specimens."""
    names = ("source_id", "formula_raw", "formula_normalized", "material_id", "specimen_id",
             "material_family", "sample_form", "processing_json", "temperature_K",
             "e_start_kV_cm", "e_end_kV_cm", "e_reported_kV_cm", "e_reported_kind", "figure_id",
             "field_path", "measurement_method", "delta_T_ad_K", "delta_t_sign_convention",
             "delta_S_J_kgK", "delta_T_jump_abs_K", "delta_S_vol_abs_kJ_K_m3")
    return json.dumps({name: getattr(record, name) for name in names}, sort_keys=True, ensure_ascii=False)


def flag_possible_duplicates(records, existing=()):
    seen = {measurement_fingerprint(r) for r in existing}
    for record in records:
        fingerprint = measurement_fingerprint(record)
        if fingerprint in seen:
            record.quality_flags = sorted(set(record.quality_flags + ["possible_duplicate_measurement"]))
        seen.add(fingerprint)


def source_cell_identity(record):
    """Exact provenance identity, independent of upload bytes and record IDs."""
    raw = record.raw_metadata_json
    original = raw.get('csv_original_row') or {}
    def value(key):
        return getattr(record, key, None) or raw.get(key) or original.get(key)
    parts = (record.doi, value('source_sha256'), value('source_sheet'),
             value('temperature_cell'), value('response_cell'))
    return parts if all(parts) else None


def same_source_observation(left, right):
    names = ('formula_raw', 'specimen_id', 'temperature_K', 'field_path',
             'e_reported_kV_cm', 'e_reported_kind', 'delta_T_jump_abs_K',
             'delta_S_vol_abs_kJ_K_m3', 'delta_T_ad_K', 'delta_S_J_kgK',
             'measurement_method', 'target_kind', 'normalization_basis', 'value_derivation', 'field_level_role')
    return all(getattr(left, k) == getattr(right, k) for k in names)
