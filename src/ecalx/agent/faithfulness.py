"""Tool-grounded reports: free provider prose cannot become a scientific claim."""
import copy
import json

INTERPRETATIONS = {
    'evidence_only':'结果是来源证据，协议不同不能混排，不能证明新组成性能。',
    'unverified_candidates':'生成组成仅为研究候选；化学检查通过不代表结构、合成或电卡性能已验证。',
    'virtual_results_only':'数值来自实际虚拟工具执行，仅对冻结演示环境有效；不是已验证新材料发现。',
    'unsupported_domain':'现有模型或模拟器不支持该物理任务；可以继续无性能承诺的材料研究。',
    'saved_not_training':'真实文件仅保存和质量分析；SAVE_ONLY禁止训练选择器读取。',
    'review_required':'模型更新仅为申请；质量审核与单独授权之后才可准入，本阶段不执行正式模型升级。'}

BOUNDARIES = {
    'production_model': 'NOT_TRAINED',
    'research_model': 'TRAINED_UNVERIFIED',
    'quantitative_prediction': 'REFUSED_NO_APPROVED_MODEL',
    'candidate_performance': 'UNKNOWN_UNVERIFIED_CHEMICAL_RULE_CANDIDATES',
    'virtual_feedback': 'SIMULATED_OR_HISTORICAL_REPLAY_NOT_REAL_MEASUREMENT',
    'notice': 'evidence_only是可核查来源证据；当前查询若无满足完整协议的可采纳候选，仅表示本次未形成可信推荐，'
              '不代表原始实验数据无价值。'
              'NOT_TRAINED仅指正式预测模型；已有研究模型TRAINED_UNVERIFIED。'
              '无法可靠量化新组成ΔT；生成规则与化学检查不能替代实测。'
              '直接实测与间接估算保留原方法；SIMULATED不是REAL_MEASUREMENT。',
}


def pointer_value(data, pointer):
    if pointer and not pointer.startswith('/'):
        raise ValueError('引用必须是JSON Pointer')
    for part in pointer.split('/')[1:] if pointer else []:
        key = part.replace('~1', '/').replace('~0', '~')
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def source_ledger(calls):
    """Extract structured sources only, never DOI-like text from a paper/prompt."""
    sources = []
    def walk(value, call_id, data_version):
        if isinstance(value, dict):
            if value.get('record_id') and (value.get('doi') or value.get('url')):
                entry = {k:value.get(k) for k in ('record_id', 'doi', 'url', 'original_row',
                         'source_id', 'source_sha256', 'source_commit', 'measurement_method')}
                entry.update(call_id=call_id, data_version=data_version)
                if entry not in sources:
                    sources.append(entry)
            for child in value.values():
                walk(child, call_id, data_version)
        elif isinstance(value, list):
            for child in value:
                walk(child, call_id, data_version)
    for call in calls:
        if call['name'] in {'finish_research', 'generate_research_report'}:
            continue
        output = call.get('output', {})
        if output.get('status') == 'OK':
            data = output['data']
            walk(data, call['id'], data.get('data_version'))
    return sources


def audit_report(report, calls):
    """Recheck exact values, source identity/DOI/version and mandatory refusal."""
    errors = []
    successful = {c['id']:c for c in calls if c.get('output', {}).get('status') == 'OK'}
    for finding in report.get('findings', []):
        try:
            call = successful[finding['call_id']]
            expected = pointer_value(call['output']['data'], finding['pointer'])
            if (json.dumps(expected, sort_keys=True, ensure_ascii=False) !=
                    json.dumps(finding['value'], sort_keys=True, ensure_ascii=False)
                    or finding['tool'] != call['name']):
                errors.append('VALUE_NOT_FROM_TOOL')
        except (KeyError, ValueError, IndexError, TypeError):
            errors.append('INVALID_CALL_OR_POINTER')
    if not report.get('findings'):
        errors.append('NO_GROUNDED_FINDINGS')
    if report.get('sources') != source_ledger(calls):
        errors.append('SOURCE_ID_DOI_VERSION_MISMATCH')
    if report.get('scientific_boundaries') != BOUNDARIES:
        errors.append('UNSUPPORTED_PREDICTION_OR_MODEL_STATE')
    if report.get('explanation') != INTERPRETATIONS.get(report.get('interpretation')):
        errors.append('UNSUPPORTED_FREE_SCIENTIFIC_TEXT')
    return {'passed':not errors, 'errors':errors,
            'source_citations_checked':len(report.get('sources', [])),
            'findings_checked':len(report.get('findings', [])),
            'method':'EXACT_TOOL_POINTER_AND_STRUCTURED_SOURCE_CHECK_NOT_SEMANTIC_LLM_EVALUATION'}


def grounded_metadata(calls):
    return {'sources':source_ledger(calls), 'scientific_boundaries':copy.deepcopy(BOUNDARIES)}
