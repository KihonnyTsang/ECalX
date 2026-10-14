"""Small explicit data projection, independent of any personal source manifests."""
from ecalx.agent.llm import redact

FACTS=('record_id','formula_raw','formula_normalized','source_id','doi','url','original_row',
       'temperature_K','e_start_kV_cm','e_end_kV_cm','e_reported_kV_cm','e_reported_kind',
       'e_delta_kV_cm','e_max_kV_cm','field_path','measurement_method','sample_form','quality_flags')

def fact(row):
    return {k:row.get(k) for k in FACTS if k in row}

class ProjectedPolicy:
    def __init__(self,root,repository):
        self.repository=repository
        self.attribution={'repository':'USER_LOCAL_DATA_NOT_INDEPENDENTLY_VERIFIED',
                          'commit':repository.data_version}
    def project(self,name,data):
        if name=='search_material_evidence':
            rows=[item['evidence'] for key in ('candidates','evidence_only') for item in data.get(key,[])]
            return {'data_version':data['data_version'],'records':[fact(r) for r in rows[:8]],
                    'truncated':len(rows)>8,'query_counts':data['query_counts']}
        if name=='generate_material_candidates':
            candidates=[]
            for c in data.get('candidates',[])[:4]:
                item={k:c.get(k) for k in ('candidate_id','status','formula_raw','formula_normalized',
                    'formula_empirical','family_rule','goal_assessment')}
                item['generation_sources']=[{**{k:s.get(k) for k in ('seed_formula','rule','parameters')},
                    'seed_sources':[fact(r) for r in s.get('seed_sources',[])[:2]]} for s in c.get('generation_sources',[])[:2]]
                candidates.append(item)
            return {'data_version':data['data_version'],'candidates':candidates,'predictions':[],
                    'M5_enabled':False,'scope':'USER_DATA_CHEMICAL_EXPLORATION'}
        if name=='validate_chemical_constraints':
            return {k:data.get(k) for k in ('formula','family','chemical_pass','structure_verified','performance','exact','sites','assumed_states')}
        raise ValueError('未准许的数据投影')

def safe(value):
    if redact(value)!=value:
        raise ValueError('出站内容包含敏感凭据标记，请移除后再研究')
