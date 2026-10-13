"""Public capability contracts. No credentials, paths, SQL or approval flags."""
from typing import Literal
from pydantic import Field, model_validator
from ecalx.experiments.contracts import Contract
from ecalx.domain.constraints import DiscoveryRequest

TOOL_NAMES = ('search_material_evidence', 'generate_material_candidates', 'validate_chemical_constraints',
              'check_model_applicability', 'create_virtual_experiment', 'select_next_candidate',
              'query_virtual_experiment', 'compare_optimization_strategies', 'import_real_experiment',
              'request_model_update', 'generate_research_report')


class Goals(Contract):
    temperature_min_K: float | None = None
    temperature_max_K: float | None = None
    e_start_kV_cm: float | None = None
    e_end_kV_cm: float | None = None
    e_min_kV_cm: float | None = None
    e_max_kV_cm: float | None = None
    delta_E_kV_cm: float | None = None
    delta_t_operator: Literal['>', '>=', '<', '<=', '='] | None = None
    delta_t_K: float | None = None
    maximize: bool = False
    material_type: str | None = Field(default=None, max_length=100)
    include_elements: list[str] = Field(default_factory=list, max_length=30)
    exclude_elements: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def scientific_rules(self):
        self.request()
        return self

    def request(self):
        return DiscoveryRequest(**self.model_dump())


class PlanningProposal(Contract):
    """Model-writable research actions only. Goals belong to the host."""
    steps: list[str] = Field(min_length=1, max_length=16)
    clarification: str | None = Field(default=None, max_length=1000)

    @model_validator(mode='after')
    def known_steps(self):
        if set(self.steps) - set(TOOL_NAMES):
            raise ValueError('计划中存在未注册工具')
        return self


class Plan(PlanningProposal):
    """Persisted host plan; legacy input must exactly match frozen Goals."""
    goals: Goals = Field(default_factory=Goals)


class Empty(Contract):
    pass


class Generate(Contract):
    max_candidates: int = Field(default=12, ge=1, le=48)


class Chemical(Contract):
    formula: str = Field(min_length=1, max_length=200)
    family: Literal['perovskite_ABO3', 'fluorite_AO2', 'spinel_AB2O4'] = 'perovskite_ABO3'


class Applicability(Contract):
    formula: str = Field(min_length=1, max_length=200)
    adapter: Literal['SIMULATED', 'HISTORICAL_REPLAY'] = 'SIMULATED'
    temperature_K: float = Field(default=303, gt=0, le=2000)
    e_start_kV_cm: float = Field(default=0, ge=0, le=1000)
    e_end_kV_cm: float = Field(default=20, ge=0, le=1000)


class VirtualPlan(Contract):
    adapter: Literal['SIMULATED', 'HISTORICAL_REPLAY'] = 'SIMULATED'
    temperature_K: float = Field(default=303, gt=0, le=2000)
    e_start_kV_cm: float = Field(default=0, ge=0, le=1000)
    e_end_kV_cm: float = Field(default=20, ge=0, le=1000)
    initial_count: int = Field(default=3, ge=2, le=10)
    additional_budget: int = Field(default=10, ge=1, le=18)
    seed: int = Field(default=42, ge=0, le=2**32-1)
    strategy: Literal['Random', 'Uniform', 'EI', 'UCB'] = 'EI'

    @model_validator(mode='after')
    def budget(self):
        from ecalx.experiments.contracts import ComparisonInput
        ComparisonInput(**self.model_dump(exclude={'seed','strategy'}), seeds=[self.seed])
        return self


class ExperimentRef(Contract):
    experiment_id: str = Field(min_length=1, max_length=100)


class Advance(ExperimentRef):
    rounds: int = Field(default=1, ge=1, le=18)


class ImportFile(Contract):
    upload_id: str = Field(min_length=1, max_length=100)
    feedback_choice: Literal['SKIP_FEEDBACK', 'SAVE_ONLY', 'REQUEST_MODEL_UPDATE'] = 'SAVE_ONLY'


class Finding(Contract):
    call_id: str = Field(min_length=1, max_length=100)
    pointer: str = Field(default='', max_length=500)


class Finish(Contract):
    findings: list[Finding] = Field(default_factory=list, max_length=16)
    interpretation: Literal['evidence_only', 'unverified_candidates', 'virtual_results_only',
                            'unsupported_domain', 'saved_not_training', 'review_required'] = 'evidence_only'


class ToolOutput(Contract):
    status: Literal['OK', 'REJECTED', 'ERROR'] = 'OK'
    # Output payloads are existing domain schemas, not model-authored JSON.
    data: dict = Field(default_factory=dict)
    error: str | None = None
    provenance: str = 'DETERMINISTIC_TOOL'
    M5_enabled: Literal[False] = False


INPUTS = dict(zip(TOOL_NAMES, (Empty, Generate, Chemical, Applicability, VirtualPlan, ExperimentRef,
                              Advance, VirtualPlan, ImportFile, ExperimentRef, Empty)))
STATEFUL = frozenset({'create_virtual_experiment', 'query_virtual_experiment',
                      'compare_optimization_strategies', 'import_real_experiment', 'request_model_update'})
DESCRIPTIONS = {
    'finish_research': 'Complete an auditable research report by citing successful scientific tool results using actual tool call IDs and JSON Pointers relative to each result data object. Cover every required receipt.',
    'search_material_evidence': 'Read verified local evidence under confirmed goals; source protocols remain distinct.',
    'generate_material_candidates': 'Generate unverified chemical candidates from real source seeds, no performance predictions.',
    'validate_chemical_constraints': 'Exact stoichiometry, element and declared site/charge checks; structure remains unknown.',
    'check_model_applicability': 'Check exact virtual domain and confirmed conditions, no quantitative prediction. M5 disabled.',
    'create_virtual_experiment': 'Budget consuming. Propose a frozen simulator and budget; human approval is required.',
    'select_next_candidate': 'Read only selection from public pool and observed GP; never exposes hidden labels.',
    'query_virtual_experiment': 'Execute frozen optimizer rounds within approved budget; human approval required.',
    'compare_optimization_strategies': 'Budget consuming fair EI versus Random, same frozen pool/seed/initial/budget. Requires approval.',
    'import_real_experiment': 'Import only a human-uploaded file by upload_id. Separate save approval and feedback choice enforced.',
    'request_model_update': 'Request review only; SAVE_ONLY needs a separately confirmed new submission. Never trains/upgrades models.',
    'generate_research_report': 'Read structured trace and exact tool results for a grounded report; no invented numbers.'}


def strict_schema(model):
    schema = model.model_json_schema()
    def visit(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object':
                node['additionalProperties'] = False
                node['required'] = list(node.get('properties', {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema


def definitions(planning=False, names=None, *, frozen_goals=None, blocking_questions=(), legacy_goals=None):
    models = {'submit_research_plan': PlanningProposal if frozen_goals is not None else Plan} if planning else {**INPUTS, 'finish_research': Finish}
    if names is not None:
        models = {name:model for name,model in models.items() if name in names}
    result = [{'type':'function', 'name':name, 'description':DESCRIPTIONS.get(name, 'Propose steps under host-frozen goals. Clarification is only for host-listed blocking questions; otherwise null.'),
             'strict':True, 'parameters':strict_schema(model)} for name, model in models.items()]
    if planning and frozen_goals is not None:
        for tool in result:
            tool['parameters']['properties']['clarification'] = (
                {'enum': list(blocking_questions)} if blocking_questions else {'type': 'null'})
    elif planning and legacy_goals is not None:
        for tool in result:
            for field, value in legacy_goals.items():
                tool['parameters']['$defs']['Goals']['properties'][field] = {'enum':[value]}
            tool['parameters']['properties']['clarification'] = {'type':'null'}
    return result
