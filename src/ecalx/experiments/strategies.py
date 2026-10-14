"""Pure selection tool: no oracle, database, evaluator, real data or file access."""
import math
import random

import numpy as np
from pydantic import Field
from .contracts import (Candidate, Contract, ExperimentError, ExperimentFeedback, StrategyParameters)


class SelectionInput(Contract):
    candidates: list[Candidate]
    observations: list[ExperimentFeedback]
    predictions: dict[str, float]
    posterior_std: dict[str, float]
    parameters: StrategyParameters
    seed: int = Field(ge=0, le=2**32-1)
    round: int = Field(ge=1)


class SelectionOutput(Contract):
    candidate_id: str
    scores: dict[str, float]
    reason: str
    parameters: StrategyParameters
    observed_best: float


def expected_improvement(mu, sigma, observed_best, xi=0.):
    """Maximization EI, incumbent is best *observed* response, in response units."""
    if not all(math.isfinite(x) for x in (mu, sigma, observed_best, xi)) or sigma < 0 or xi < 0:
        raise ExperimentError('EI需要有限输入、非负标准差与xi')
    improvement = mu - observed_best - xi
    if sigma == 0:
        return max(improvement, 0.)
    z = improvement / sigma
    cdf = .5 * math.erfc(-z/math.sqrt(2))
    pdf = math.exp(-.5*z*z)/math.sqrt(2*math.pi)
    return max(0., improvement*cdf + sigma*pdf)


def select_next_candidate(request: SelectionInput) -> SelectionOutput:
    candidates = [c.model_dump() for c in request.candidates]
    observed = {o.candidate_id for o in request.observations}
    by_id = {c['candidate_id']: c for c in candidates}
    if len(by_id) != len(candidates) or not observed or observed - by_id.keys():
        raise ExperimentError('选择器仅接受冻结池及其已揭示反馈')
    if any(o.provenance == 'REAL_MEASUREMENT' for o in request.observations):
        raise ExperimentError('虚拟优化器不接受真实反馈或SAVE_ONLY数据')
    remaining = [c for c in candidates if c['candidate_id'] not in observed]
    if not remaining:
        raise ExperimentError('冻结池已耗尽')
    best = max(o.value for o in request.observations)
    rng = random.Random(f'{request.seed}:{request.round}')
    strategy = request.parameters.strategy
    if strategy == 'Random':
        scores = {c['candidate_id']: 1/len(remaining) for c in remaining}
        selected = rng.choice(remaining)['candidate_id']
        reason = '固定seed均匀随机抽取未查询候选；score为抽取概率，不使用标签或GP选点'
    else:
        scores = {}
        pool = np.asarray([c['features'] for c in candidates])
        scale = np.ptp(pool, axis=0)
        scale[scale == 0] = 1
        known = np.asarray([c['features'] for c in candidates if c['candidate_id'] in observed]) / scale
        for c in remaining:
            cid = c['candidate_id']
            if strategy == 'Uniform':
                scores[cid] = float(np.min(np.linalg.norm(np.asarray(c['features'])/scale-known, axis=1)))
            else:
                if cid not in request.predictions or cid not in request.posterior_std:
                    raise ExperimentError('缺少已查询反馈拟合的GP后验；旧E1模型需要重新拟合')
                mu, sigma = request.predictions[cid], request.posterior_std[cid]
                if sigma < 0:
                    raise ExperimentError('后验标准差不能为负')
                scores[cid] = (expected_improvement(mu, sigma, best, request.parameters.xi)
                               if strategy == 'EI' else mu + request.parameters.kappa*sigma)
        maximum = max(scores.values())
        # Stable pool order, deterministic seed tie-breaking; no oracle tie-break.
        selected = rng.choice([cid for cid, score in scores.items() if score == maximum])
        reason = {'Uniform': '公开池各维range缩放后的maximin欧氏距离，覆盖离已查询点最远区域',
                  'EI': f'最大化EI：基于已观测最优={best:.8g}，xi={request.parameters.xi} K，零方差使用确定性改进',
                  'UCB': f'最大化mu+kappa*sigma，kappa={request.parameters.kappa}；sigma为未校准GP内部后验标准差'}[strategy]
    return SelectionOutput(candidate_id=selected, scores=scores, reason=reason,
                           parameters=request.parameters, observed_best=best)
