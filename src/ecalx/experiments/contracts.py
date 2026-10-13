"""M4-E1 tool contracts. No LLM or production predictor is imported here."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Provenance = Literal['SIMULATED', 'HISTORICAL_REPLAY', 'REAL_MEASUREMENT']
FeedbackChoice = Literal['SKIP_FEEDBACK', 'SAVE_ONLY', 'REQUEST_MODEL_UPDATE']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True, validate_default=True)


class ExperimentError(ValueError):
    """Expected, user-visible tool failure; transactions roll back on errors."""


class CreateVirtualInput(Contract):
    adapter: Literal['SIMULATED', 'HISTORICAL_REPLAY'] = 'SIMULATED'
    seed: int = Field(default=42, ge=0, le=2**32-1)
    initial_count: int = Field(default=3, ge=2, le=10)
    temperature_K: float = Field(default=303, gt=0)
    e_start_kV_cm: float = Field(default=0, ge=0)
    e_end_kV_cm: float = Field(default=20, ge=0)
    authorization_id: str
    noise_std_K: float = Field(default=0, ge=0, le=1)
    noise_seed: int = Field(default=0, ge=0, le=2**32-1)
    candidate_keys: list[str] | None = None

    @model_validator(mode='after')
    def path(self):
        if self.adapter == 'SIMULATED' and self.e_start_kV_cm == self.e_end_kV_cm:
            raise ValueError('模拟实验需要非零电场变化')
        if self.adapter == 'HISTORICAL_REPLAY' and (self.temperature_K, self.e_start_kV_cm, self.e_end_kV_cm) != (303, 50, 0):
            raise ValueError('BSMT回放仅支持已核验303K、50→0kV/cm；不能生成其他历史协议')
        if self.adapter == 'HISTORICAL_REPLAY' and self.noise_std_K:
            raise ValueError('历史标签不能添加模拟噪声')
        return self


class Candidate(Contract):
    candidate_id: str
    formula: str
    features: list[float]
    source_record_id: str | None = None


class ExperimentFeedback(Contract):
    candidate_id: str
    value: float
    unit: Literal['K'] = 'K'
    target: str
    provenance: Provenance
    source: dict
    seed: int
    query_id: str


class VirtualExperimentOutput(Contract):
    experiment_id: str
    adapter: Literal['SIMULATED', 'HISTORICAL_REPLAY']
    seed: int
    frozen_sha256: str
    candidates: list[Candidate]
    initial_feedback: list[ExperimentFeedback]
    conditions: dict


class QueryVirtualInput(Contract):
    experiment_id: str
    candidate_id: str
    request_id: str = Field(min_length=1, max_length=200)
    authorization_id: str


class LoopInput(Contract):
    experiment_id: str
    rounds: int = Field(default=1, ge=1, le=50)
    authorization_id: str


class LoopOutput(Contract):
    experiment_id: str
    completed_rounds: int
    observations: int
    latest_model_version: str
    trace: list[dict]
    M5_enabled: Literal[False] = False


class StrategyParameters(Contract):
    strategy: Literal['Random', 'Uniform', 'EI', 'UCB'] = 'Random'
    xi: float = Field(default=0, ge=0)
    kappa: float = Field(default=2, ge=0)


class ConfigureOptimizationInput(Contract):
    experiment_id: str
    parameters: StrategyParameters = Field(default_factory=StrategyParameters)
    additional_budget: int = Field(default=10, ge=1, le=50)
    authorization_id: str


class AdvanceOptimizationInput(Contract):
    experiment_id: str
    rounds: int = Field(default=1, ge=1, le=50)
    request_id: str = Field(min_length=1, max_length=200)
    authorization_id: str


class ControlOptimizationInput(Contract):
    experiment_id: str
    action: Literal['PAUSE', 'RESUME']
    authorization_id: str


class ComparisonInput(Contract):
    adapter: Literal['SIMULATED', 'HISTORICAL_REPLAY'] = 'SIMULATED'
    seeds: list[int] = Field(default_factory=lambda: list(range(10)), min_length=1, max_length=50)
    initial_count: int = Field(default=3, ge=2, le=10)
    additional_budget: int = Field(default=10, ge=1, le=50)
    temperature_K: float = Field(default=303, gt=0)
    e_start_kV_cm: float = Field(default=0, ge=0)
    e_end_kV_cm: float = Field(default=20, ge=0)
    noise_std_K: float = Field(default=0, ge=0, le=1)
    noise_seed: int = Field(default=0, ge=0, le=2**32-1)
    candidate_keys: list[str] | None = None
    xi: float = Field(default=0, ge=0)
    kappa: float = Field(default=2, ge=0)

    @model_validator(mode='after')
    def seed_plan(self):
        if len(set(self.seeds)) != len(self.seeds) or any(not 0 <= s <= 2**32-1 for s in self.seeds):
            raise ValueError('随机种子列表必须唯一且在uint32范围内')
        pool_size = len(self.candidate_keys) if self.candidate_keys is not None else (21 if self.adapter == 'SIMULATED' else 12)
        if self.initial_count + self.additional_budget > pool_size:
            raise ValueError('比较总预算超过候选池；拒绝在无效计划下消费初始查询')
        CreateVirtualInput(**self.model_dump(exclude={'seeds', 'additional_budget', 'xi', 'kappa'}),
                           seed=self.seeds[0], authorization_id='contract_validation_only')
        return self


class ImportRealInput(Contract):
    filename: str
    formula: str | None = None
    batch_id: str | None = None
    specimen_id: str | None = None
    source: str | None = None
    feedback_choice: FeedbackChoice
    authorization_id: str


class RealImportOutput(Contract):
    experiment_id: str
    provenance: Literal['REAL_MEASUREMENT'] = 'REAL_MEASUREMENT'
    feedback_choice: FeedbackChoice
    raw_sha256: str | None
    row_count: int
    quality_issues: list[str]
    request_id: str | None
    trainable: Literal[False] = False


class ReviewInput(Contract):
    request_id: str
    decision: Literal['APPROVE', 'REJECT']
    reviewer: str = Field(min_length=1)
    note: str = Field(min_length=1)
    authorization_id: str


class RequestModelUpdateInput(Contract):
    experiment_id: str
    authorization_id: str


class UpdateRequestOutput(Contract):
    request_id: str
    status: Literal['PENDING_REVIEW', 'REJECTED', 'REVIEWED', 'AUTHORIZED']
    experiment_id: str
    model_training_started: Literal[False] = False


class AuthorizeUpdateInput(Contract):
    request_id: str
    authorization_id: str


class TrainingSelectionOutput(Contract):
    provenance: Literal['REAL_MEASUREMENT'] = 'REAL_MEASUREMENT'
    contains_simulated_labels: Literal[False] = False
    records: list[dict]


class ExperimentQueryInput(Contract):
    mode: Literal['VIRTUAL', 'REAL']
    experiment_id: str
    candidate_id: str | None = None
    request_id: str | None = None
    authorization_id: str | None = None


class ExperimentPoint(Contract):
    candidate_id: str | None = None
    value: float | None
    unit: str | None
    target: str | None
    provenance: Provenance
    conditions: dict
    source: dict
    quality_issues: list[str] = Field(default_factory=list)


class ExperimentQueryOutput(Contract):
    experiment_id: str
    mode: Literal['VIRTUAL', 'REAL']
    points: list[ExperimentPoint]
    model_training_started: Literal[False] = False
