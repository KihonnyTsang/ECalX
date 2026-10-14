"""Unified experiment query interface with isolated mode-specific adapters.

Raw label access is not exposed. The future Research Agent receives this facade,
never Store.authorize(), connections, private environment JSON or SQL handles.
"""
from .contracts import (ExperimentError, ExperimentPoint, ExperimentQueryInput,
                        ExperimentQueryOutput, QueryVirtualInput)
from .real import RealLaboratory
from .virtual import VirtualLaboratory


class ExperimentSystem:
    def __init__(self, storage_root, project_root):
        self.virtual = VirtualLaboratory(storage_root, project_root)
        self.real = RealLaboratory(storage_root)

    def query_experiment(self, request: ExperimentQueryInput) -> ExperimentQueryOutput:
        if request.mode == 'VIRTUAL':
            if not all((request.candidate_id, request.request_id, request.authorization_id)):
                raise ExperimentError('虚拟查询必须有候选、请求ID和人工授权')
            f = self.virtual.query_virtual_experiment(QueryVirtualInput(experiment_id=request.experiment_id,
                candidate_id=request.candidate_id, request_id=request.request_id, authorization_id=request.authorization_id))
            point = ExperimentPoint(candidate_id=f.candidate_id, value=f.value, unit=f.unit, target=f.target,
                provenance=f.provenance, conditions=self.virtual.get(request.experiment_id).conditions,
                source=f.source | {'query_id': f.query_id, 'seed': f.seed})
            return ExperimentQueryOutput(experiment_id=request.experiment_id, mode='VIRTUAL', points=[point])
        if request.candidate_id is not None:
            raise ExperimentError('真实实验按实际提交读取；不能用候选ID编造测量')
        record = next((r for r in self.real.list_experiments() if r['experiment_id'] == request.experiment_id), None)
        if record is None:
            raise ExperimentError('未知真实实验ID')
        points = [ExperimentPoint(value=r['value'], unit=r['unit'], target=r['target'] or r['measurement_type'],
            provenance='REAL_MEASUREMENT',
            conditions={k: r[k] for k in ('temperature_K', 'e_start_kV_cm', 'e_end_kV_cm', 'field_kV_cm',
                                          'direction', 'method', 'sample_form', 'frequency_Hz', 'time_s')},
            source={'source': r['source'], 'raw_sha256': record['raw_sha256'],
                    'original_row': r['original_row'], 'specimen_id': r['specimen_id'], 'batch_id': r['batch_id']},
            quality_issues=r['quality_issues']) for r in record['records']]
        return ExperimentQueryOutput(experiment_id=request.experiment_id, mode='REAL', points=points)
