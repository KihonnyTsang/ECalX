"""Host-owned goals and strict proposal validation. No model-output repairs."""
from ecalx.parsing.requirements import parse_request
from .contracts import Goals, Plan, PlanningProposal
from .capabilities import unresolved_preferences
from .llm import ProviderError


def input_goals(original):
    parsed = parse_request({'natural_language': original})
    goals = Goals(**{k: v for k, v in parsed.request.to_dict().items()
                     if k in Goals.model_fields}).model_dump()
    return goals, unresolved_preferences(parsed)


def accept_proposal(value, frozen_goals, blocking_questions=(), *, legacy=True):
    if not isinstance(value, dict):
        raise ProviderError('计划必须为结构化参数', code='PLAN_REJECTED')
    if 'goals' in value:
        if (not legacy or set(value) != {'goals', 'steps', 'clarification'} or
                not isinstance(value['goals'], dict) or set(value['goals']) != set(frozen_goals)):
            raise ProviderError('模型不能写入Goals', code='PLAN_REJECTED')
        plan = Plan.model_validate(value, strict=True)
        if plan.goals.model_dump() != frozen_goals:
            raise ProviderError('模型Goals与宿主冻结值不一致；拒绝且不修正', code='PLAN_REJECTED')
        proposal = PlanningProposal(steps=plan.steps, clarification=plan.clarification)
    else:
        if set(value) != {'steps', 'clarification'}:
            raise ProviderError('规划参数必须完整匹配Schema', code='PLAN_REJECTED')
        proposal = PlanningProposal.model_validate(value, strict=True)
    if blocking_questions and proposal.clarification is None:
        raise ProviderError('宿主阻塞问题尚未解决，不能跳过clarification', code='PLAN_REJECTED')
    if proposal.clarification is not None and proposal.clarification not in blocking_questions:
        raise ProviderError('非阻塞说明或科研偏好不能作为clarification', code='PLAN_REJECTED')
    if len(set(proposal.steps)) != len(proposal.steps):
        raise ProviderError('规划步骤重复', code='PLAN_REJECTED')
    # This is host assembly from an accepted proposal, never correction of model Goals.
    return Plan(goals=Goals.model_validate(frozen_goals, strict=True),
                **proposal.model_dump()).model_dump()
