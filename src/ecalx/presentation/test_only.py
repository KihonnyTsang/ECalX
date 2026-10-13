"""Validate display-only test packets; never execute the named capabilities."""
from ecalx.agent.contracts import Finish, Goals, INPUTS, PlanningProposal, ToolOutput


ALLOWED = frozenset({
    'search_material_evidence', 'generate_material_candidates',
    'validate_chemical_constraints', 'finish_research',
})


def validate_packet(packet):
    """Public display approval cannot override scientific or execution boundaries."""
    task = packet['task']
    for response in packet['responses']:
        if (response.get('model') != 'TEST_ONLY'
                or response.get('evidence_mode') != 'TEST_ONLY'
                or response.get('actual_requests', 0) != 0):
            raise ValueError('Saved synthetic responses cannot claim actual model requests')
        if 'simulation_status' in response and response['simulation_status'] != 'TEST_ONLY_AUTHORED_SIMULATED':
            raise ValueError('Synthetic narrative rounds must remain explicitly simulated')
    if packet['original_guard'] is not None or packet['posthoc_review'] is not None:
        raise ValueError('TEST_ONLY cannot carry live acceptance or posthoc evidence')
    goals = Goals.model_validate(task['frozen_goals'])
    plan = task['plan']
    if goals.model_dump() != Goals.model_validate(plan['goals']).model_dump():
        raise ValueError('Saved plan must match host-frozen Goals')
    PlanningProposal.model_validate({k: plan[k] for k in ('steps', 'clarification')})
    if set(plan['steps']) - ALLOWED:
        raise ValueError('Read-only TEST_ONLY display excludes experiments and training')
    ids = set()
    calls = task.get('calls', [])
    for call in calls:
        name = call['name']
        if name not in ALLOWED or call['id'] in ids:
            raise ValueError('Unsupported capability or duplicate receipt ID')
        ids.add(call['id'])
        (Finish if name == 'finish_research' else INPUTS[name]).model_validate(call['arguments'])
        ToolOutput.model_validate(call['output'])
        data = call['output']['data']
        if (call['evidence_mode'] != 'TEST_ONLY' or data['evidence_mode'] != 'TEST_ONLY'
                or call['execution_status'] != 'TEST_ONLY_SIMULATED_NOT_EXECUTED'
                or call.get('elapsed_seconds') is not None
                or call['output'].get('M5_enabled') is not False):
            raise ValueError('TEST_ONLY receipts must be explicitly unexecuted with M5 disabled')
        for candidate in data.get('candidates', []):
            compliance = candidate['performance_compliance']
            if (candidate['evidence_mode'] != 'TEST_ONLY'
                    or candidate['status'] != 'generated_unverified'
                    or compliance.get('status') != 'unknown'
                    or compliance.get('prediction') is not None
                    or compliance.get('delta_T_ad_K') is not None):
                raise ValueError('Synthetic candidates must retain unknown performance')
        sources = [*data.get('evidence_preview', []), *data.get('source_records', {}).values()]
        for source in sources:
            if any(source.get(k) is not None for k in (
                    'doi', 'temperature_K', 'e_start_kV_cm', 'e_end_kV_cm',
                    'e_reported_kV_cm', 'delta_T_ad_K')):
                raise ValueError('TEST_ONLY sources cannot present measured or literature values')
        if name == 'validate_chemical_constraints':
            if data.get('structure_verified') is not False or data.get('performance') != 'unknown':
                raise ValueError('Chemical test doubles do not validate structure or performance')
        if name == 'finish_research' and 'report' in data:
            if data['report'].get('M5_enabled') is not False or data['report'].get('performance') != 'unknown':
                raise ValueError('TEST_ONLY report must preserve unknown performance and disabled M5')
    for call in calls:
        if call['name'] == 'finish_research':
            for ref in call['arguments']['findings']:
                linked = [c for c in calls if c['id'] == ref['call_id'] and c is not call
                          and c['output']['status'] == 'OK']
                if len(linked) != 1:
                    raise ValueError('Findings must cite a unique successful saved receipt')
                node = linked[0]['output']['data']
                pointer = ref['pointer']
                if pointer and not pointer.startswith('/'):
                    raise ValueError('Invalid JSON Pointer')
                for token in pointer.split('/')[1:]:
                    token = token.replace('~1', '/').replace('~0', '~')
                    node = node[int(token)] if isinstance(node, list) else node[token]
    return packet
