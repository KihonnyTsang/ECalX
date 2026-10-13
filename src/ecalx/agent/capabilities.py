"""State-derived capabilities, independent of provider/model decisions."""
from .contracts import STATEFUL, definitions


def unresolved_preferences(parsed):
    pending = [p for p in parsed.pending if not p.startswith('未解析文本')]
    r = parsed.request
    if any(getattr(r, f) is not None for f in ('e_max_kV_cm','e_min_kV_cm','e_start_kV_cm','e_end_kV_cm','delta_E_kV_cm')):
        pending = [p for p in pending if not p.startswith('低电场')]
    if r.temperature_min_K is not None and r.temperature_max_K is not None:
        pending = [p for p in pending if not p.startswith(('室温附近','宽温区'))]
    return pending


def available_tools(task):
    if task['status'] == 'DRAFT' and not task.get('plan'):
        return definitions(planning=True, frozen_goals=task.get('frozen_goals'),
                           blocking_questions=task.get('blocking_questions', ()))
    if task['status'] != 'RUNNING' or not task.get('plan_confirmed'):
        return []
    names = set(task['plan']['steps'])
    if not task.get('virtual_ids'):
        names -= {'select_next_candidate', 'query_virtual_experiment'}
    if not task.get('uploads'):
        names.discard('import_real_experiment')
    update_requested = task.get('update_subject') or any(
        u['choice'] == 'REQUEST_MODEL_UPDATE' for u in task.get('uploads', []))
    if not task.get('real_ids') or not update_requested:
        names.discard('request_model_update')
    if any(c.get('output', {}).get('status') == 'OK' for c in task['calls']):
        names.add('finish_research')
    return definitions(names=names)


def capability_view(task):
    return [{'tool':t['name'], 'permission':'PROPOSE_REQUIRES_EXACT_HUMAN_APPROVAL'
             if t['name'] in STATEFUL else 'READ_ONLY_OR_CONTROL'}
            for t in available_tools(task)]
