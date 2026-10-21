"""Deterministic attribution and admission rules; never a model confidence score.

No section heading implies ownership. Mixed citation/author signals abstain.
This module does not read private configuration or make network requests.
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

POLICY_VERSION = 'paper-safety-3'
OWN = 'OWN_EXPERIMENT'
CITED = 'CITED_OTHER_WORK'
UNCERTAIN = 'UNCERTAIN'
THEORY = 'THEORY_SIMULATION'
# A small held-out corpus is evidence about that corpus, not deployment approval.
# Release approval requires a separately audited, version-bound validation report.
AUTO_IMPORT_ENABLED = False

CITATION = r'(?:\[\s*\d+(?:\s*[-–,;]\s*\d+)*\s*\]|\b(?:refs?\.?|references?)\s*\d+|\bet al\.?|\breported by\b|\bprevious(?:ly)? (?:reported|stud(?:y|ies)|work)\b)'
OWN_CUE = r'(?:\bwe (?:have )?(?:measur\w*|obtain\w*|observ\w*|demonstrat\w*|report\w*|investigat\w*|find|found)\b|\bour (?:sample|ceramic|film|crystal|measurement|experiment|result)\w*\b|\b(?:in )?(?:this|present) (?:work|study|experiment)\b)'
THEORY_CUE = r'first.principles|density.functional|molecular dynamics|Monte Carlo|phase.field|Landau|theoretical (?:model|prediction)|simulat(?:ion|ed|ions)'


def attribution(row):
    text = row['location']['quote']
    if row.get('evidence_only'):
        return {'kind':UNCERTAIN,'rule':'unparsed_page_evidence_only','evidence':dict(row['location']),
                'cited_reference':None,'original_measurement_doi':None}
    context = text+' '+row.get('table_context',{}).get('quote','')
    ref = (row.get('reference') or '').strip()
    own_ref = bool(re.fullmatch(r'(?:this|present) (?:work|study)', ref, re.I))
    cited_ref = bool(ref and not own_ref and re.search(r'\d|et al|ref|previous', ref, re.I))
    signals=[]
    for match in re.finditer(CITATION,text,re.I):
        # Miller directions are not reference numbers. Require an explicit
        # crystallographic context; a bare [111] remains a citation/uncertainty.
        around=text[max(0,match.start()-45):match.end()+45]
        orientation=(bool(re.fullmatch(r'\[[01]{3}\]',match.group())) and
                     bool(re.search(r'orient\w*|crystal\w*|direction|\bsample\b',around,re.I)))
        if not orientation:signals.append(match.group())
    cited = cited_ref or bool(signals)
    own = own_ref or bool(re.search(OWN_CUE, text, re.I))
    author_signal=own
    if own and not own_ref and not re.search(r'\b(?:measur\w*|obtain\w*|observ\w*|report\w*|found|find|demonstrat\w*)\b',text,re.I):
        own=False
    theory = bool(re.search(THEORY_CUE, context, re.I))
    if cited_ref:
        kind, reason = CITED, 'table_reference_identifies_other_work'
    elif author_signal and cited:
        kind, reason = UNCERTAIN, 'mixed_author_and_citation_context'
    elif cited:
        kind, reason = CITED, 'explicit_citation_or_reported_by'
    elif theory:
        kind, reason = THEORY, 'explicit_theory_or_simulation_context'
    elif own:
        kind, reason = OWN, 'explicit_author_experiment_claim'
    else:
        kind, reason = UNCERTAIN, 'no_unambiguous_author_experiment_claim'
    # Calculation without a measured-data/indirect-method link is not an experiment.
    if kind == OWN and re.search(r'calculat|predict', text, re.I):
        if not (row['values'].get('method') == 'indirect' and re.search(r'measured|experimental|P.?E loop|polarization', text, re.I)):
            kind, reason = UNCERTAIN, 'calculation_without_experimental_method_link'
    return {'kind': kind, 'rule': reason, 'evidence': dict(row['location']),
            'cited_reference': ref or None, 'original_measurement_doi': None if kind != OWN else row['values'].get('doi')}


def assess(row, draft=None):
    """Fail closed, including per-field locators and CSV scientific validation."""
    row['attribution'] = attribution(row)
    v = row['values']; reasons = []
    kind = row['attribution']['kind']
    if kind != OWN: reasons.append('ownership:' + kind)
    if row.get('pairing', {}).get('status') != 'EXPLICIT': reasons.append('pairing_unresolved')
    for key in ('formula', 'temperature', 'temperature_unit', 'e_delta', 'field_unit', 'method'):
        if v.get(key) in (None, '', 'unknown'): reasons.append('missing:' + key)
    if row['location']['kind']!='table' and v.get('method') in {'direct','indirect'}:
        if not re.search(r'(?:direct|indirect) (?:method|measure\w*)|measured (?:directly|indirectly)',row['location']['quote'],re.I):
            reasons.append('measurement_method_definition_unresolved')
    if not (v.get('specimen_id') or v.get('sample_form')): reasons.append('sample_identity_unresolved')
    if not any(v.get(k) is not None for k in ('delta_t', 'delta_s')): reasons.append('no_scalar_outcome')
    if v.get('delta_t') is not None:
        # Backend slot is delta_T_ad: explicit adiabatic definition is required.
        if not re.search(r'adiabatic|ΔT_?ad', row['location']['quote'], re.I): reasons.append('temperature_quantity_definition_unresolved')
    if v.get('delta_s') is not None and not re.search(r'ΔS|entropy change', row['location']['quote'], re.I):
        reasons.append('entropy_quantity_definition_unresolved')
    if row.get('warnings'): reasons.append('extraction_warning')
    if v.get('data_kind')=='SYNTHETIC_TEST_DATA':reasons.append('synthetic_test_data')
    doi = v.get('doi')
    if not doi or not re.fullmatch(r'10\.\d{4,9}/\S+', doi): reasons.append('missing_or_invalid_doi')
    if row['location'].get('page', 0) < 1: reasons.append('missing_physical_page')
    if draft is not None and not re.fullmatch(r'[a-f0-9]{64}', draft.sha256): reasons.append('missing_pdf_hash')
    for key in ('formula', 'temperature', 'temperature_unit', 'e_delta', 'field_unit', 'method',
                'delta_t', 'delta_t_unit', 'delta_s', 'delta_s_unit', 'specimen_id', 'sample_form'):
        if v.get(key) in (None, '', 'unknown'): continue
        src = row.get('field_sources', {}).get(key, {})
        if not src.get('span') or not src.get('bbox') or not src.get('quote'): reasons.append('missing_field_locator:' + key)
    if not reasons:
        from .import_data import preview
        import pandas as pd
        csv = pd.DataFrame([v]).fillna('').to_csv(index=False).encode()
        try:
            checked = preview(csv, 'paper-rules.csv', {k:k for k in v})
            if checked['errors']: reasons.append('csv_validation_failed')
        except (ValueError, TypeError): reasons.append('csv_validation_failed')
    row['admission'] = {'route': 'CITATION' if kind == CITED else 'AUTO_ELIGIBLE' if not reasons else 'REVIEW',
                        'reasons': reasons, 'policy_version': POLICY_VERSION}
    return row


def rules_digest():
    root = Path(__file__).parent
    files = ('paper_extract.py', 'paper_policy.py', 'paper_batch.py', 'import_data.py')
    return hashlib.sha256(b''.join(name.encode() + (root/name).read_bytes() for name in files)).hexdigest()


def auto_gate():
    return {'enabled': AUTO_IMPORT_ENABLED, 'policy_version': POLICY_VERSION,
            'rules_sha256': rules_digest(),
            'reason': '独立验收样本尚不足以批准无人逐条确认；仅可预检和人工确认，引用数据始终隔离。'}
