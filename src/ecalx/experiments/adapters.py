"""Virtual environment sources. Hidden labels never enter candidate metadata."""
import csv
import hashlib
import io
import math
import random
from pathlib import Path

from .contracts import ExperimentError

# Pinned, already reviewed M4-D artifact, not an arbitrary uploaded history file.
BSMT_SHA256 = '8a55dfe3e47cc41ac6a1e1d8d8ca73a7772e7ac1cb8b7735ab2145e5ded69801'
SIMULATOR = {
    'version': 'maxwell-toy-1', 'rho_kg_m3': 6000., 'Cp_J_kgK': 400.,
    'b_C_per_VmK': 2e-9, 'width_K': 18., 'Tc_K': '280 + 80*x',
    'equation': 'T*b*exp(-(T-(280+80*x))**2/(2*width**2))*(E_end_SI**2-E_start_SI**2)/(2*rho*Cp)',
    'assumption': 'dP/dT=-b*E*exp(-(T-Tc(x))^2/(2*w^2)); reversible toy Maxwell integral; no calibration',
    'notice': 'SIMULATED；参数与Tc关系是演示假设，不是BST实测事实或性能模型；无真实发现声明',
}


def build_environment(root, request):
    candidates, labels = [], {}
    if request.adapter == 'SIMULATED':
        conditions = {'temperature_K': request.temperature_K,
                      'e_start_kV_cm': request.e_start_kV_cm, 'e_end_kV_cm': request.e_end_kV_cm,
                      'target': 'toy_signed_delta_T_K', 'simulator': SIMULATOR,
                      'scope': 'illustrative_nominal_BST_grid_x_0_to_1_not_user_goal_or_verified_solid_solution'}
        for i in range(21):
            x = i / 20
            cid = f'sim-{i:03}'
            formula = 'BaTiO3' if i == 0 else 'SrTiO3' if i == 20 else f'Ba{1-x:.2f}Sr{x:.2f}TiO3'
            candidates.append({'candidate_id': cid, 'formula': formula,
                               'features': [x], 'source_record_id': None})
            p = SIMULATOR
            value = request.temperature_K*p['b_C_per_VmK']*math.exp(
                -(request.temperature_K-(280+80*x))**2/(2*p['width_K']**2))*(
                    (request.e_end_kV_cm*1e5)**2-(request.e_start_kV_cm*1e5)**2)/(2*p['rho_kg_m3']*p['Cp_J_kgK'])
            if not math.isfinite(value):
                raise ExperimentError('模拟方程溢出；请调整明确实验条件')
            labels[cid] = {'value': value, 'source': {'simulator_version': p['version'], 'equation': p['equation']}}
        if request.noise_std_K:
            conditions['noise'] = {'std_K': request.noise_std_K, 'seed': request.noise_seed,
                                   'policy': 'one frozen Gaussian draw per candidate; never per query'}
            for cid, label in labels.items():
                label['latent_value'] = label['value']
                noise = random.Random(f"maxwell-toy-1:{request.noise_seed}:{cid}").gauss(0, request.noise_std_K)
                label['value'] += noise
                label['source']['noise_policy'] = 'candidate_fixed_Gaussian'
                label['source']['noise_std_K'] = request.noise_std_K
    else:
        raw = (Path(root)/'docs/bsmt_results/measurements.csv').read_bytes()
        if hashlib.sha256(raw).hexdigest() != BSMT_SHA256:
            raise ExperimentError('已核验BSMT工件缺失或SHA不符；不生成历史标签')
        conditions = {'temperature_K': 303., 'e_start_kV_cm': 50., 'e_end_kV_cm': 0.,
                      'target': 'cooling_magnitude_K', 'sample_form': 'bulk ceramic',
                      'measurement_method': 'direct', 'field_path': 'unloading',
                      'dataset_sha256': BSMT_SHA256,
                      'notice': '12组成/1论文、已曝光历史开发标签回放，非盲测或新材料验证'}
        for i, row in enumerate(csv.DictReader(io.StringIO(raw.decode()))):
            cid = f'history-{i:03}'
            candidates.append({'candidate_id': cid, 'formula': row['formula'],
                               'features': [float(row['Sr_A_fraction']), float(row['Mn_B_fraction'])],
                               'source_record_id': row['record_id']})
            labels[cid] = {'value': float(row['cooling_magnitude_K']),
                           'source': {k: row[k] for k in ('doi', 'original_row', 'source_sha256',
                                                       'paper_sha256', 'numeric_verification', 'label_exposure')}}
        if len(candidates) != 12:
            raise ExperimentError('BSMT回放候选数不符合冻结版本')
    if request.candidate_keys is not None:
        keys = set(request.candidate_keys)
        if not keys or len(keys) != len(request.candidate_keys) or keys - labels.keys():
            raise ExperimentError('生成候选池含未知或重复候选；不插值或伪造实验标签')
        candidates = [c for c in candidates if c['candidate_id'] in keys]
        labels = {c['candidate_id']: labels[c['candidate_id']] for c in candidates}
    if len(candidates) < request.initial_count:
        raise ExperimentError('初始样本数超过候选池')
    return candidates, labels, conditions
