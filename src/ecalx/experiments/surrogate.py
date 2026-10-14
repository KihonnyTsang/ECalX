"""Fixed-kernel Gaussian Process demo; depends only on base pandas' NumPy.

This function sees ONLY queried feedback. It has no database or oracle handle.
It is not the legacy research model and never predicts general material inputs.
"""
import hashlib
from pathlib import Path

import numpy as np
from .storage import digest, VERSION


def feedback_digest(feedback):
    # Scientific data identity excludes run UUIDs, query request IDs and clocks.
    return digest([{k: r[k] for k in ('value', 'unit', 'target', 'provenance', 'source', 'seed')} |
                   {'candidate_key': r['candidate_id'].rsplit(':', 1)[-1]} for r in feedback])


def fit_observed(candidates, feedback, conditions):
    by_id = {c['candidate_id']: c for c in candidates}
    ids = [r['candidate_id'] for r in feedback]
    pool = np.asarray([c['features'] for c in candidates], dtype=float)
    low, scale = pool.min(axis=0), np.ptp(pool, axis=0)
    scale[scale == 0] = 1
    x = (np.asarray([by_id[c]['features'] for c in ids])-low)/scale
    y = np.asarray([r['value'] for r in feedback])
    mean, std = float(y.mean()), float(y.std()) or 1.
    z = (pool-low)/scale
    kernel = lambda a, b: np.exp(-.5*np.sum(((a[:, None]-b[None, :])/.25)**2, axis=2))
    alpha = np.linalg.solve(kernel(x, x)+.01*np.eye(len(x)), (y-mean)/std)
    prediction = mean + std * (kernel(z, x)@alpha)
    covariance = kernel(x, x)+.01*np.eye(len(x))
    cross = kernel(z, x)
    # Latent posterior variance, excluding observation noise; never calibrated CI.
    variance = np.maximum(0., 1.-np.sum(cross*np.linalg.solve(covariance, cross.T).T, axis=1))*std**2
    # No claimed calibrated variance, validation metrics or real material ranking.
    return {'code_version': VERSION,
            'tool_code_sha256': {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                                  for name in ('surrogate.py', 'virtual.py', 'adapters.py', 'contracts.py', 'strategies.py', 'optimization.py')}, 'model_type': 'fixed_RBF_GaussianProcess',
            'numpy_version': np.__version__, 'hyperparameters': {'length_scale': .25, 'noise_variance': .01, 'optimizer': None},
            'training_candidate_ids': ids, 'training_feedback': feedback,
            'training_sha256': feedback_digest(feedback), 'training_provenance': sorted({r['provenance'] for r in feedback}),
            'contains_simulated_labels': any(r['provenance'] == 'SIMULATED' for r in feedback),
            'target': conditions['target'], 'feature_low': low.tolist(), 'feature_scale': scale.tolist(),
            'y_mean': mean, 'y_scale': std, 'posterior_alpha': alpha.tolist(),
            'predictions': {c['candidate_id']: float(v) for c, v in zip(candidates, prediction)},
            'posterior_std': {c['candidate_id']: float(v) for c, v in zip(candidates, np.sqrt(variance))},
            'uncertainty': 'uncalibrated; intervals not reported',
            'independent_test': {'status': 'NOT_PERFORMED', 'metrics': None}, 'production_enabled': False}
