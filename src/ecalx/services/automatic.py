"""Goal-driven, bounded chemical exploration. No ML imports or performance ranking."""
from fractions import Fraction
from itertools import combinations

from ecalx.materials.chemistry import parse_formula, exact_key, composition_key
from ecalx.materials.generator import REGISTRY, GenerationRequest, generate, material_display
from ecalx.services.discovery import FORM_ALIASES, coverage, unmet_constraints

VERSION = 'automatic-research-0.1.0'
# Engineering search budget, not measured solubility or a scientifically optimal range.
GRID = (Fraction(1, 100), Fraction(1, 20), Fraction(1, 10))
FAMILY_ALIASES = {'钙钛矿': 'perovskite_ABO3', 'ABO3': 'perovskite_ABO3',
                  'AO2': 'fluorite_AO2', '尖晶石': 'spinel_AB2O4', 'AB2O4': 'spinel_AB2O4'}


def reference(r):
    return {k: getattr(r, k) for k in ('record_id', 'source_id', 'doi', 'url', 'original_row',
        'formula_raw', 'formula_normalized', 'temperature_K', 'e_reported_kind', 'e_reported_kV_cm',
        'e_start_kV_cm', 'e_end_kV_cm', 'e_delta_kV_cm', 'e_max_kV_cm', 'field_path', 'measurement_method', 'sample_form')}


def temperature_hit(r, request):
    return r.temperature_K is not None and (
        request.temperature_min_K is None or r.temperature_K >= request.temperature_min_K) and (
        request.temperature_max_K is None or r.temperature_K <= request.temperature_max_K)


def field_hit(r, request):
    # Only endpoint facts can satisfy bounds/path. An explicit delta scalar may
    # satisfy amplitude, but never an Emax or endpoint constraint.
    return not any(x in unmet_constraints(r, request) for x in
                   ('e_start_kV_cm', 'e_end_kV_cm', '电场下限', '电场上限', '电场变化幅度'))


def source_coverage(records, request):
    temperature_requested = request.temperature_min_K is not None or request.temperature_max_K is not None
    field_requested = any(getattr(request, k) is not None for k in
        ('e_start_kV_cm', 'e_end_kV_cm', 'e_min_kV_cm', 'e_max_kV_cm', 'delta_E_kV_cm'))
    wanted_form = FORM_ALIASES.get(request.material_type, request.material_type)
    form_hits = [r for r in records if wanted_form and r.sample_form
                 and r.sample_form.casefold() == wanted_form.casefold()]
    return {'records': len(records), 'nominal_compositions': len({exact_key(parse_formula(
            r.formula_normalized or r.formula_raw)) for r in records}),
        'doi_count': len({r.doi for r in records if r.doi}),
        'temperature_point_hits': sum(temperature_hit(r, request) for r in records) if temperature_requested else None,
        'field_fact_hits': sum(field_hit(r, request) for r in records) if field_requested else None,
        'joint_temperature_field_hits': sum(temperature_hit(r, request) and field_hit(r, request) for r in records)
            if temperature_requested or field_requested else None,
        'material_form_hits': len(form_hits) if request.material_type else None,
        'scope': 'source records only; point hits do not verify generated compositions or whole working interval'}


def proposed_plans(seed, rule, observed, request):
    """Isovalent site substitutions plus exact two-operation charge compensation.

    A host-removal fraction is used (not absolute site doping). Coupled x2 is
    solved from seed amounts and declared charge changes, then chemistry checked.
    """
    operations = []
    for site, elements in rule.sites.items():
        for host in sorted(seed.keys() & elements.keys()):
            for dopant in elements:
                if dopant == host or dopant in request.exclude_elements:
                    continue
                delta = elements[dopant] - elements[host]
                if delta == 0 and dopant not in observed[site] and dopant not in request.include_elements:
                    continue
                operations.append((host, dopant, seed[host]*delta))
    plans = []
    for host, dopant, charge in operations:
        if charge == 0:
            plans += [[{'host': host, 'dopant': dopant, 'fraction': str(x)}] for x in GRID]
    for first, second in combinations(operations, 2):
        h1, d1, q1 = first; h2, d2, q2 = second
        if q1*q2 >= 0 or h1 == h2 or d1 == h2 or d2 == h1:
            continue
        for x1 in GRID:
            x2 = -q1*x1/q2
            if 0 < x2 <= GRID[-1]:
                plans.append([{'host': h1, 'dopant': d1, 'fraction': str(x1)},
                              {'host': h2, 'dopant': d2, 'fraction': str(x2)}])
    # Required new elements first, then singles/coupled interleaved later by search.
    plans.sort(key=lambda p: (-len(set(request.include_elements) & {o['dopant'] for o in p}), str(p)))
    return plans[:1000]


def explore_materials(request, repository, *, max_seeds=8, max_candidates=48):
    if not 1 <= max_seeds <= 32 or not 1 <= max_candidates <= 200:
        raise ValueError('Search budget: 1–32 seeds, 1–200 candidates')
    records = repository.records()
    known, groups, skipped = {}, {}, []
    observed = {name: {site: {} for site in rule.sites} for name, rule in REGISTRY.rules.items()}
    for r in records:
        try:
            amounts = parse_formula(r.formula_normalized or r.formula_raw)
        except ValueError:
            skipped.append({'record_id': r.record_id, 'reason': 'unparsed_formula'})
            continue
        key = exact_key(amounts)
        known.setdefault(key, []).append(reference(r))
        if not (r.doi or r.url) or 'quarantined' in r.quality_flags:
            continue
        for family, rule in REGISTRY.rules.items():
            try:
                seed, sites, _ = rule.validate(amounts)
            except ValueError:
                continue
            group = groups.setdefault((family, key), {'amounts': seed, 'records': [], 'family': family})
            group['records'].append(r)
            for element, site in sites.items():
                observed[family][site].setdefault(element, {})[r.doi or r.url] = reference(r)
    all_valid = [r for g in groups.values() for r in g['records']]
    wanted_family = FAMILY_ALIASES.get(request.material_type, request.material_type
                       if request.material_type in REGISTRY.rules else None)
    wanted_form = FORM_ALIASES.get(request.material_type, request.material_type)
    eligible = []
    for g in groups.values():
        if set(request.exclude_elements) & g['amounts'].keys():
            continue
        if wanted_family and g['family'] != wanted_family:
            continue
        # Unsupported forms produce an explicit evidence gap, not oxide candidates
        # masquerading as a polymer or an arbitrary requested material category.
        if request.material_type and not wanted_family and not any(
                r.sample_form and r.sample_form.casefold() == wanted_form.casefold() for r in g['records']):
            continue
        rule_elements = {e for elements in REGISTRY.rules[g['family']].sites.values() for e in elements} | {'O'}
        if set(request.include_elements) - rule_elements:
            continue
        g['coverage'] = source_coverage(g['records'], request)
        eligible.append(g)
    eligible.sort(key=lambda g: (-(g['coverage']['joint_temperature_field_hits'] or 0),
        -(g['coverage']['temperature_point_hits'] or 0), -g['coverage']['doi_count'],
        -len(g['records']), g['family'], exact_key(g['amounts'])))
    # Round-robin element systems/families prevents the densest curve taking all seeds.
    buckets = {}
    for g in eligible:
        buckets.setdefault((g['family'], tuple(sorted(g['amounts']))), []).append(g)
    selected = []
    while buckets and len(selected) < max_seeds:
        for key in list(buckets):
            if len(selected) == max_seeds:
                break
            selected.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
    result = {'schema_version': VERSION, 'status': 'RESEARCH_ONLY', 'request': request.to_dict(),
        'data_version': repository.data_version, 'M5_enabled': False, 'production_recommendation_enabled': False,
        'production_model_state': 'NOT_TRAINED', 'predictions': [], 'model_version': None,
        'ordering': 'deterministic_research_task_queue_no_performance_ranking',
        'search_budget': {'max_seeds': max_seeds, 'max_candidates': max_candidates, 'host_fraction_grid': list(map(str, GRID)),
            'interpretation': 'bounded local exploration, not solubility limits or exhaustive search'},
        'coverage': {'database': coverage(records), 'rule_supported': source_coverage(all_valid, request),
                     'eligible_seed_compositions': len(eligible)},
        'seeds': [], 'candidates': [], 'rejected': [], 'unparsed_records': skipped,
        'limitations': ['化学规则有效、相关文献证据、性能满足目标分别判断；未发现排除证据不代表符合。',
            '默认库的家族偏倚是数据范围，不是输入限制；其他家族需要真实来源及显式化学规则。',
            '记录数不是独立实验数；库未收录不代表全球新颖。',
            '主替代1%–10%原host为有限搜索网格，不是已验证固溶范围；耦合另一比例按声明价态求解，可小于1%。',
            '结构、价态、相稳定、制备、击穿、形态及电卡性能均需实验证实；不自动调用研究模型。'],
        'unverified_requirements': list(request.confirmed_ambiguities)}
    if request.natural_language and not request.confirmed_ambiguities:
        result['natural_language_scope'] = 'confirmed parsed goals only; original text retained'
    pools = []
    for g in selected:
        family, seed = g['family'], g['amounts']
        rs = sorted(g['records'], key=lambda r: (not temperature_hit(r, request), r.record_id))
        unique = {}
        for r in rs:
            unique.setdefault(r.doi or r.url, reference(r))
        sources = list(unique.values())
        seed_info = {'seed_id': rs[0].record_id, 'formula_raw': rs[0].formula_raw,
            **material_display(seed, family), 'family_rule': family, 'sources': sources,
            'coverage': g['coverage'], 'selection_basis': '目标条件点覆盖→DOI数→记录数；按家族/元素体系轮询，非性能排名'}
        result['seeds'].append(seed_info)
        proposals = proposed_plans(seed, REGISTRY.rules[family], observed[family], request)
        generated = generate(rs[0].formula_normalized or rs[0].formula_raw, sources, family, proposals,
            GenerationRequest(include=request.include_elements, exclude=request.exclude_elements,
                              material_type=request.material_type))
        result['rejected'] += [{'seed_id': rs[0].record_id, **rejection} for rejection in generated['rejected']]
        singles, coupled = [], []
        for c in generated['candidates']:
            matches = known.get(c['exact_composition_key'], [])
            c.update(known_database_matches=matches, database_presence='present' if matches else 'not_found_in_current_database',
                     database_match_precision='exact rational atomic fractions; nominal composition only')
            plan = c['generation_sources'][0]['parameters']
            dopant_refs = {}
            for op in plan:
                site = next(s for s, es in REGISTRY.rules[family].sites.items() if op['host'] in es)
                dopant_refs[op['dopant']] = list(observed[family][site].get(op['dopant'], {}).values())
            c['seed_id'] = rs[0].record_id
            c['seed_formula_raw'] = rs[0].formula_raw
            c['chemical_checks'] = {'stoichiometry': 'pass', 'elements': 'pass', 'sites': 'pass',
                                    'assumed_charge_balance': 'pass', 'structure_confirmed': False}
            c['evidence_status'] = {'related_seed_experiments': True,
                'exact_composition_in_database': bool(matches), 'dopant_site_related_records': dopant_refs,
                'candidate_performance_at_goals': 'unknown', 'synthesis_success': 'unknown'}
            c['performance_compliance'] = {'status': 'unknown', 'reason':
                '无逐候选、目标条件下经过核验的可靠实测或获准模型证据；同名组成命中也不证明同样品/同协议。'}
            c['goal_assessment'] = {'elements': 'satisfied_under_nominal_formula',
                'temperature': 'unknown' if request.temperature_min_K is not None or request.temperature_max_K is not None else 'unspecified',
                'field': 'unknown' if any(getattr(request, k) is not None for k in
                    ('e_start_kV_cm','e_end_kV_cm','e_min_kV_cm','e_max_kV_cm','delta_E_kV_cm')) else 'unspecified',
                'delta_T': 'unknown' if request.delta_t_K is not None or request.maximize else 'unspecified',
                'material_type': 'unknown' if request.material_type else 'unspecified',
                'unresolved_text': list(request.confirmed_ambiguities)}
            c['design_basis'] = {'substitution_kind': 'coupled_charge_compensation' if len(plan)>1 else 'isovalent_site_substitution',
                'primary_host_fraction_search_interval': ['1/100','1/10'],
                'interval_evidence': 'engineering exploration only; no verified continuous solid solution range',
                'coupled_fraction_basis': 'exact charge compensation; secondary fraction may be below 1/100',
                'observed_dopant_site_amount_ranges': {op['dopant']: {
                    'min': str(min(gg['amounts'][op['dopant']] for gg in groups.values()
                                   if gg['family']==family and op['dopant'] in gg['amounts'])),
                    'max': str(max(gg['amounts'][op['dopant']] for gg in groups.values()
                                   if gg['family']==family and op['dopant'] in gg['amounts'])),
                    'scope': 'observed nominal amounts across this family; not solubility or performance range'}
                    if dopant_refs[op['dopant']] else {'min': None, 'max': None, 'scope': 'unknown; rule hypothesis only'}
                    for op in plan},
                'seed_coverage': g['coverage'], 'dopant_evidence_scope': 'same declared family/site, not candidate performance'}
            c['research_priority'] = {'task': '核查已有组成协议' if matches else '组成邻域补测',
                'basis': {'known_EC_relation': '真实电卡实验种子及明确替代链',
                    'seed_experimental_coverage': g['coverage'],
                    'database_novelty': '当前库已有名义组成' if matches else '当前库无精确名义组成记录；全球新颖性未知',
                    'chemical_constraints': '计量/位点/声明电荷检查通过；真实价态与结构待测',
                    'preparability_relation': '种子有实验来源；候选可制备性未知'},
                'scientific_score': None, 'performance_rank': None, 'synthesis_probability': None}
            c['unverified_constraints'] = [k for k,v in c['goal_assessment'].items() if v == 'unknown']
            (coupled if len(plan)>1 else singles).append(c)
        # Both substitution strategies get finite queue positions, without model scores.
        pool = []
        while singles or coupled:
            if singles: pool.append(singles.pop(0))
            if coupled: pool.append(coupled.pop(0))
        pools.append(pool)
    seen = {}
    while any(pools) and len(seen) < max_candidates:
        for pool in pools:
            if not pool or len(seen) >= max_candidates:
                continue
            c = pool.pop(0)
            if c['exact_composition_key'] in seen:
                previous = seen[c['exact_composition_key']]
                previous['generation_sources'] += c['generation_sources']
            else:
                seen[c['exact_composition_key']] = c
    result['candidates'] = list(seen.values())
    result['search_truncated'] = bool(any(pools)) or len(eligible) > len(selected)
    result['message'] = (f'自动选择{len(selected)}个用户提供的来源种子（未经独立核验；SYNTHETIC_TEST_DATA仅软件测试），提出{len(seen)}个未验证研究候选；性能符合性未知。'
                         if seen else '数据覆盖或规则支持不足，没有可生成且符合元素/材料类型要求的候选；未虚构来源或性能。')
    return result


def exploration_csv(result):
    import csv
    import io
    import json
    from ecalx.services.export import safe_csv_cell
    fields = ['candidate_id', 'formula_raw', 'formula_normalized', 'formula_empirical', 'site_occupancies',
        'site_fractions', 'stoichiometry_exact', 'family_rule', 'status', 'seed_id', 'seed_formula_raw',
        'generation_sources', 'chemical_checks', 'database_presence', 'known_database_matches',
        'evidence_status', 'performance_compliance', 'goal_assessment', 'design_basis', 'research_priority',
        'verification_gaps', 'request', 'data_version', 'ordering', 'M5_enabled']
    output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
    for c in result['candidates']:
        row = {**c, **{k: result[k] for k in ('request','data_version','ordering','M5_enabled')}}
        writer.writerow({k: safe_csv_cell(json.dumps(row.get(k), ensure_ascii=False)
            if isinstance(row.get(k), (dict,list)) else row.get(k)) for k in fields})
    return '\ufeff'+output.getvalue()


def exploration_markdown(result):
    import json
    lines = ['# ECalX 自动探索研究清单', '', 'RESEARCH_ONLY · M5禁用 · 无性能排名或性能承诺。', '',
             result['message'], '', '需求：'+json.dumps(result['request'], ensure_ascii=False), '',
             '数据库版本：'+result['data_version'], '', '## 来源种子', '']
    for s in result['seeds']:
        lines += ['- '+s['formula_normalized']+'；'+s['selection_basis'],
                  '  来源：'+json.dumps(s['sources'], ensure_ascii=False)]
    lines += ['', '## 候选研究任务', '']
    for c in result['candidates']:
        lines += ['- '+c['formula_normalized']+' · 化学规则通过 · 性能符合性未知',
                  '  位点：'+json.dumps(c['site_occupancies'], ensure_ascii=False),
                  '  设计：'+json.dumps(c['design_basis'], ensure_ascii=False),
                  '  优先级依据：'+json.dumps(c['research_priority'], ensure_ascii=False),
                  '  待验证目标：'+json.dumps(c['goal_assessment'], ensure_ascii=False)]
    lines += ['', '## 限制', '']+['- '+s for s in result['limitations']]
    return '\n'.join(lines)+'\n'
