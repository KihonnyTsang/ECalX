"""Pure saved-run projections for Streamlit. Never constructs an agent/client."""
import json
from pathlib import Path

import hashlib


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def saved_run_view(task, responses):
    calls=task.get('calls',[])
    generated=[c for call in calls if call['name']=='generate_material_candidates'
        and call['output']['status']=='OK' for c in call['output']['data'].get('candidates',[])]
    checks=[call for call in calls if call['name']=='validate_chemical_constraints'
            and call['output']['status']=='OK']
    rows=[]
    for candidate in generated:
        receipts=[c for c in checks if c['arguments'].get('formula') in
                  {candidate.get('formula'),candidate.get('formula_normalized'),candidate.get('formula_empirical')}
                  and c['arguments'].get('family')==candidate.get('family_rule')]
        check=receipts[0]['output']['data'] if len(receipts)==1 else {}
        rows.append({'组成':candidate.get('formula'),
            '身份':candidate.get('status'),'性能':candidate.get('performance_compliance',{}).get('status'),
            '独立chemical_pass':check.get('chemical_pass'), '结构已验证':check.get('structure_verified'),
            '来源种子':candidate.get('seed_id'),'种子公式':' / '.join(
                s['seed_formula'] for s in candidate.get('generation_sources',[])),
            'candidate_id':candidate['candidate_id'],'化学收据':receipts[0]['id'] if len(receipts)==1 else 'UNKNOWN_OR_AMBIGUOUS'})
    sources={}
    for call in calls:
        if call['output']['status']!='OK': continue
        data=call['output']['data']
        for row in [*data.get('evidence_preview',[]),*data.get('source_records',{}).values()]:
            sources[row['record_id']]={**row,'data_version':data.get('data_version')}
    source_rows=[{'record_id':s['record_id'],'DOI':s.get('doi'),
        'DOI链接':'https://doi.org/'+s['doi'] if s.get('doi') else None,
        '历史组成':s.get('formula_normalized'),'温度K':s.get('temperature_K'),
        'E起点kV/cm':s.get('e_start_kV_cm'),'E终点kV/cm':s.get('e_end_kV_cm'),
        '报告电场值':s.get('e_reported_kV_cm'),'电场语义':s.get('e_reported_kind'),
        '场路径':s.get('field_path'),'测量方法':s.get('measurement_method'),
        '历史ΔT_K':s.get('delta_T_ad_K'),'ΔT依据':s.get('delta_t_basis'),
        '数据版本':s.get('data_version'),'质量标记':s.get('quality_flags')} for s in sources.values()]
    final=next((r for r in reversed(responses) if not r.get('calls') and r.get('visible_text')),None)
    final_status='NOT_RETURNED'
    if final:
        attempts=final.get('metrics',{}).get('attempts',[])
        last=attempts[-1] if attempts else {}
        choices=last.get('response_metadata',{}).get('choices',[])
        final_status='COMPLETE_STOP' if (last.get('response_complete') and len(choices)==1
            and choices[0].get('finish_reason')=='stop') else 'INCOMPLETE_OR_UNKNOWN'
    tools_ok=bool(calls) and all(c['output']['status']=='OK' for c in calls)
    finish=any(c['name']=='finish_research' and c['output']['status']=='OK' for c in calls)
    return {'candidates':rows,'sources':source_rows,'final_response':final,
        'tool_status':'SUCCEEDED_WITH_FINISH' if tools_ok and finish else
            'PARTIAL_OR_FAILED' if calls else 'NOT_EXECUTED',
        'summary_response_status':final_status,
        'human_scientific_review':'PENDING_INDEPENDENT_SCIENTIFIC_REVIEW'}


def bound_posthoc_review(root, directory):
    """Only display a saved review bound to this run and current validator code."""
    root, directory=Path(root),Path(directory)
    path=root/'reports/m4e412/posthoc_review.json'
    if not path.is_file(): return None
    try:
        review=json.loads(path.read_text())
        task=json.loads((directory/'task.json').read_text())
        if review.get('task_id')!=task['id']: return None
        hashes=review['source_files_sha256']
        required={str((directory/name).relative_to(root)) for name in
            ('task.json','response_05.json','summary_facts.json','summary_guard.json','structured_report.json','wire_05.body')}
        if not required<=hashes.keys(): return None
        for relative,expected in hashes.items():
            source=(root/relative).resolve()
            if not (source.is_relative_to(directory.resolve()) or
                    source==(root/'reports/m4e411/final_diagnosis.json').resolve()): return None
            if sha(source)!=expected: return None
        if review['validator_sha256']!=sha(root/'src/ecalx/agent/m4e411.py'): return None
        if review['reviewer_sha256']!=sha(root/'src/ecalx/agent/summary_review.py'): return None
    except (OSError, KeyError, ValueError, TypeError):
        return None
    return review
