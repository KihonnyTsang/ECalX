"""Original in-memory software fixture; no released demo payload or scientific data."""
import hashlib
import json
from ecalx.agent.contracts import Goals


def make_packet():
    goals = Goals(exclude_elements=['Pb']).model_dump()
    source = {'record_id':'TEST_ONLY_SOURCE', 'doi':None, 'formula_normalized':'TEST_ONLY_PLACEHOLDER',
              'temperature_K':None, 'e_start_kV_cm':None, 'e_end_kV_cm':None,
              'delta_T_ad_K':None, 'quality_flags':['TEST_ONLY']}
    candidate = {'candidate_id':'TEST_ONLY_CANDIDATE', 'formula':'TEST_ONLY_FORMULA',
                 'formula_normalized':'TEST_ONLY_FORMULA', 'formula_empirical':'TEST_ONLY_FORMULA',
                 'family_rule':'perovskite_ABO3', 'evidence_mode':'TEST_ONLY',
                 'status':'generated_unverified', 'performance_compliance':
                     {'status':'unknown','prediction':None,'delta_T_ad_K':None},
                 'seed_id':source['record_id'], 'generation_sources':
                     [{'seed_source_ids':[source['record_id']], 'seed_formula':'TEST_ONLY_PLACEHOLDER'}],
                 'chemical_checks':{'test_double':'not_executed'},'verification_gaps':['TEST_ONLY']}
    names = ['search_material_evidence', 'generate_material_candidates',
             'validate_chemical_constraints', 'finish_research']
    refs = [{'call_id':'TEST_ONLY_CALL_'+str(i), 'pointer':''} for i in range(3)]
    arguments = [{}, {'max_candidates':1}, {'formula':candidate['formula'],'family':candidate['family_rule']},
                 {'findings':refs, 'interpretation':'unverified_candidates'}]
    data = [{'evidence_preview':[source]}, {'candidates':[candidate]},
            {'candidate_id':candidate['candidate_id'], 'chemical_pass':True,
             'structure_verified':False, 'performance':'unknown'},
            {'report':{'M5_enabled':False, 'performance':'unknown','findings':refs,
                       'candidate_ids':[candidate['candidate_id']], 'task_id':'TEST_ONLY_TASK'}}]
    calls = [{'id':'TEST_ONLY_CALL_'+str(i), 'name':name, 'arguments':args,
              'evidence_mode':'TEST_ONLY', 'execution_status':'TEST_ONLY_SIMULATED_NOT_EXECUTED',
              'elapsed_seconds':None, 'output':{'status':'OK','M5_enabled':False,
                  'provenance':'TEST_ONLY', 'data':{**d, 'evidence_mode':'TEST_ONLY'}}}
             for i,(name,args,d) in enumerate(zip(names,arguments,data))]
    return {'summary':{'client_mode':'TEST_ONLY','task_id':'TEST_ONLY_TASK','status':'TEST_ONLY'},
            'task':{'id':'TEST_ONLY_TASK','client_mode':'TEST_ONLY','provider':'TEST_ONLY',
                    'M5_enabled':False,'original':'TEST_ONLY in-memory software test',
                    'frozen_goals':goals, 'plan':{'steps':names[:-1],'clarification':None,'goals':goals.copy()},
                    'calls':calls}, 'responses':[], 'original_guard':None,'posthoc_review':None}


def write_bundle(folder, packet, *, approved_test_double=False):
    """Approval strings exist only in pytest temporary test doubles, never in demo/public."""
    folder.mkdir(parents=True)
    raw = json.dumps(packet, ensure_ascii=False).encode()
    (folder/'payload.json').write_bytes(raw)
    review = {'schema_version':2, 'status':'OWNER_REVIEW', 'review_basis':'OWNER_REVIEW',
              'evidence_mode':'TEST_ONLY','owner':'TEST_ONLY_OWNER',
              'content_author':'TEST_ONLY_OWNER','independent_reviewer':'','owner_approval':'',
              'reviewed_at':'','review_record':'','rights_review':'PENDING','privacy_review':'PENDING',
              'payload_sha256':hashlib.sha256(raw).hexdigest(),
              'public_scope':[{'path':'demo/public/payload.json','sha256':hashlib.sha256(raw).hexdigest()}]}
    if folder.name != 'public':
        review = {k:v for k,v in review.items() if k not in ('owner','review_basis','public_scope')}
        review.update(schema_version=1,status='PENDING_INDEPENDENT_REVIEW')
    (folder/'review.json').write_text(json.dumps(review))
    if approved_test_double:
        # This entire release, scope and attestation exist only under pytest tmp_path.
        root = folder.parent.parent
        (root/'audit').mkdir()
        manifest = root/'audit/SHA256SUMS'
        manifest.write_text(''.join(hashlib.sha256((folder/n).read_bytes()).hexdigest()+'  demo/public/'+n+'\n'
                                    for n in ('payload.json','review.json')))
        receipt = {**review,'status':'OWNER_APPROVED','rights_review':'APPROVED','privacy_review':'APPROVED',
                   'owner_approval':'TEST_ONLY_OWNER_APPROVAL','reviewed_at':'2026-10-13T12:00:00+08:00',
                   'review_record':'TEST_ONLY_TEMPORARY_APPROVAL_DOUBLE_NOT_REAL_USER_APPROVAL',
                   'release_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
                   'public_scope':['audit/SHA256SUMS','demo/public/payload.json','demo/public/review.json']}
        (root/'OWNER_APPROVAL.json').write_text(json.dumps(receipt))
        return receipt
    return review
