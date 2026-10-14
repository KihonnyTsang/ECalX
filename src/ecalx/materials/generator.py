"""Independent chemical-rule proposals. Never imports a performance model."""
from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import sha256
import json

from ecalx.materials.chemistry import composition_key, display_formula, parse_formula, exact_key
from ecalx.materials.formulas import validate_symbols

VERSION = 'chemical-rules-0.1.1'


@dataclass(frozen=True)
class FamilyRule:
    name: str
    sites: dict  # site -> {element: assumed integer oxidation state}
    occupancy: dict
    oxygen: int
    premises: tuple[str, ...]

    def validate(self, amounts):
        if 'O' not in amounts:
            raise ValueError('Oxide rule requires oxygen')
        scale = amounts['O'] / self.oxygen
        normalized = {e: n / scale for e, n in amounts.items()}
        assigned, states = {}, {}
        for element in normalized.keys() - {'O'}:
            matches = [site for site, states_by_element in self.sites.items() if element in states_by_element]
            if len(matches) != 1:
                raise ValueError('Unsupported or ambiguous site: ' + element)
            assigned[element] = matches[0]
            states[element] = self.sites[matches[0]][element]
        for site, required in self.occupancy.items():
            if sum(n for e, n in normalized.items() if assigned.get(e) == site) != required:
                raise ValueError('Site stoichiometry mismatch: ' + site)
        charge = sum(normalized[e] * state for e, state in states.items()) - 2 * self.oxygen
        if charge:
            raise ValueError('Charge imbalance under declared oxidation states; no defect compensation inferred')
        return normalized, assigned, {**states, 'O': -2}


class RuleRegistry:
    def __init__(self):
        self.rules = {}

    def register(self, rule):
        if rule.name in self.rules or set(rule.sites) != set(rule.occupancy):
            raise ValueError('Duplicate rule or mismatched sites')
        elements = [e for site in rule.sites.values() for e in site]
        validate_symbols(elements)
        if len(elements) != len(set(elements)) or 'O' in elements:
            raise ValueError('Site assignment must be unambiguous in v1')
        self.rules[rule.name] = rule


REGISTRY = RuleRegistry()
REGISTRY.register(FamilyRule('perovskite_ABO3',
    {'A': {'Ba': 2, 'Sr': 2, 'Ca': 2, 'Pb': 2, 'Na': 1, 'K': 1, 'Bi': 3, 'La': 3},
     'B': {'Ti': 4, 'Zr': 4, 'Hf': 4, 'Sn': 4, 'Nb': 5, 'Ta': 5, 'Mg': 2, 'Sc': 3}},
    {'A': 1, 'B': 1}, 3, ('Assumed ABO3 site allocation and O2-; actual structure unknown',
                         'No vacancies, mixed valence or sintering additives inferred')))
REGISTRY.register(FamilyRule('fluorite_AO2',
    {'A': {'Ce': 4, 'Zr': 4, 'Hf': 4, 'Ti': 4}}, {'A': 1}, 2,
    ('Assumed tetravalent AO2; polymorph, reduction and oxygen vacancies unverified',)))
REGISTRY.register(FamilyRule('spinel_AB2O4',
    {'A': {'Mg': 2, 'Zn': 2, 'Ni': 2, 'Co': 2, 'Mn': 2}, 'B': {'Al': 3, 'Cr': 3, 'Fe': 3}},
    {'A': 1, 'B': 2}, 4, ('Assumed normal spinel site allocation; inversion and mixed valence unknown',)))


def material_display(amounts, family, registry=REGISTRY):
    """Reading formula with exact site coefficients, separate from parser syntax.

    [p/q] denotes a non-terminating rational coefficient, never a rounded recipe.
    """
    rule = registry.rules[family]
    normalized, assigned, _ = rule.validate(amounts)
    sites = {site: {e: str(normalized[e]) for e in elements if assigned.get(e) == site}
             for site, elements in rule.sites.items()}
    def term(element, amount):
        n = Fraction(amount)
        d = n.denominator
        for prime in (2, 5):
            while d % prime == 0:
                d //= prime
        return display_formula({element: n}) if d == 1 else f'{element}[{n}]'
    parts = []
    for occupants in sites.values():
        text = ''.join(term(e, n) for e, n in occupants.items())
        parts.append('('+text+')' if len(occupants) > 1 else text)
    return {'formula_normalized': ''.join(parts)+f'O{rule.oxygen}',
            'formula_empirical': display_formula(normalized), 'site_occupancies': sites,
            'site_fractions': {s: {e: str(Fraction(n)/rule.occupancy[s]) for e, n in occ.items()}
                               for s, occ in sites.items()},
            'formula_display_convention': 'site_normalized; [p/q] denotes exact rational coefficient'}


@dataclass
class GenerationRequest:
    include: tuple = ()
    exclude: tuple = ()
    material_type: str | None = None
    # Atomic fractions of all atoms (oxygen included); one-sided limits allowed.
    fraction_bounds: dict = field(default_factory=dict)

    def validate(self):
        validate_symbols((*self.include, *self.exclude, *self.fraction_bounds))
        if set(self.include) & set(self.exclude):
            raise ValueError('Conflicting include/exclude elements')
        for low, high in self.fraction_bounds.values():
            if any(v is not None and not 0 <= float(v) <= 1 for v in (low, high)):
                raise ValueError('Atomic-fraction bounds must be finite in [0,1]')
            if low is not None and high is not None and low > high:
                raise ValueError('Reversed atomic-fraction bounds')


def generate(seed_formula, seed_sources, family, substitutions, request=None, known=None, registry=REGISTRY):
    """Single or coupled substitution plans; x is the fraction of each host removed.

    substitutions = [[{'host':'Ba','dopant':'Sr','fraction':'0.1'}], ...]
    known maps normalized composition keys to source record/DOI metadata.
    A seed source is traceability supplied by caller, not independently verified here.
    """
    request = request or GenerationRequest(); request.validate()
    if not seed_sources or any(not s.get('record_id') or not (s.get('doi') or s.get('url')) for s in seed_sources):
        raise ValueError('A traceable known seed record and DOI/URL are required')
    if family not in registry.rules:
        raise ValueError('Unsupported family; register an explicit family rule first')
    rule = registry.rules[family]
    seed, sites, _ = rule.validate(parse_formula(seed_formula))
    known = known or {}
    result, rejected = {}, []
    if len(substitutions) > 1000:
        raise ValueError('At most 1000 plans per run')
    for plan in substitutions:
        try:
            if not plan or len(plan) > 8:
                raise ValueError('Each plan requires 1–8 explicit substitutions')
            amounts, removed = seed.copy(), {}
            for operation in plan:
                host, dopant = operation['host'], operation['dopant']
                validate_symbols((host, dopant))
                x = Fraction(str(operation['fraction']))
                if not 0 < x <= 1 or host == dopant or host not in sites:
                    raise ValueError('Invalid host, dopant or host fraction')
                site = sites[host]
                if dopant not in rule.sites[site]:
                    raise ValueError('Dopant has no declared compatible site/valence')
                amount = seed[host] * x
                removed[host] = removed.get(host, 0) + amount
                if removed[host] > seed[host]:
                    raise ValueError('Host over-substitution')
                amounts[host] -= amount
                amounts[dopant] = amounts.get(dopant, 0) + amount
            amounts = {e: n for e, n in amounts.items() if n}
            amounts, _, states = rule.validate(amounts)
            if not set(request.include) <= amounts.keys() or set(request.exclude) & amounts.keys():
                raise ValueError('Element constraint not met')
            total = sum(amounts.values())
            for element, (low, high) in request.fraction_bounds.items():
                value = float(amounts.get(element, 0) / total)
                if (low is not None and value < low) or (high is not None and value > high):
                    raise ValueError('Atomic-fraction constraint not met: ' + element)
            key = composition_key(amounts)
            identity = exact_key(amounts)
            provenance = {'seed_formula': seed_formula, 'seed_sources': seed_sources,
                          'rule': family, 'rule_version': VERSION, 'parameters': plan,
                          'premises': list(rule.premises),
                          'rule_definition': {'sites': rule.sites, 'occupancy': rule.occupancy, 'oxygen': rule.oxygen}}
            if identity in result:
                if provenance not in result[identity]['generation_sources']:
                    result[identity]['generation_sources'].append(provenance)
                continue
            result[identity] = {'candidate_id': 'chem-' + sha256(identity.encode()).hexdigest()[:20],
                'formula': display_formula(amounts), 'composition_key': key, 'exact_composition_key': identity,
                'formula_raw': display_formula(amounts), **material_display(amounts, family, registry),
                'stoichiometry_exact': {e: str(n) for e, n in amounts.items()},
                'family_rule': family, 'status': 'generated_unverified',
                'evidence_basis': 'chemical_rules_only', 'oxidation_states_assumed': states,
                'charge_balance': 'neutral_under_declared_assumptions',
                'generation_sources': [provenance], 'known_database_matches': known.get(key, []),
                'database_presence': 'present' if key in known else 'not_found_in_supplied_index',
                'database_match_precision': '8 decimal atomic fractions; nominal composition only',
                'requested_material_type': request.material_type,
                'material_type_status': 'unverified' if request.material_type else 'unspecified',
                'unverified_constraints': ['material_type'] if request.material_type else [],
                'sample_form': None, 'processing_evidence': [], 'structure_evidence': [],
                'candidate_electrocaloric_evidence': [], 'predictions': [], 'model_version': None,
                'verification_gaps': ['preparation', 'structure', 'phase_stability', 'electrocaloric_performance',
                                      'applicable_validated_model']}
        except (ValueError, KeyError, ZeroDivisionError) as error:
            rejected.append({'parameters': plan, 'reason': str(error)})
    return {'generator_version': VERSION, 'status': 'R_AND_D_ONLY',
            'request': {'include': request.include, 'exclude': request.exclude,
                        'material_type': request.material_type, 'fraction_bounds': request.fraction_bounds},
            'candidates': sorted(result.values(), key=lambda c: c['exact_composition_key']),
            'rejected': rejected, 'ordering': 'lexical_composition_key_no_performance_ranking',
            'production_recommendation_enabled': False}


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path, help='JSON seed/sources/family/substitutions/constraints')
    parser.add_argument('--database', type=Path, help='Existing ECalX SQLite; opened read-only')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.request.read_text())
    known, source_records = {}, {}
    if args.database:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            for (row,) in connection.execute('SELECT payload FROM records ORDER BY record_id'):
                record = json.loads(row)
                source_records[record['record_id']] = record
                key = composition_key(parse_formula(record.get('formula_normalized') or record['formula_raw']))
                known.setdefault(key, []).append({'record_id': record['record_id'], 'doi': record.get('doi'),
                                                 'method': record.get('measurement_method')})
        for source in payload['seed_sources']:
            record = source_records.get(source['record_id'])
            if record is None or composition_key(parse_formula(payload['seed_formula'])) != composition_key(
                    parse_formula(record.get('formula_normalized') or record['formula_raw'])):
                raise ValueError('Seed source not present or composition differs in supplied database')
            if source.get('doi') and source['doi'] != record.get('doi'):
                raise ValueError('Seed DOI differs from supplied database')
    result = generate(payload['seed_formula'], payload['seed_sources'], payload['family'],
                      payload['substitutions'], GenerationRequest(**payload.get('constraints', {})), known)
    result['database_index'] = {'path': str(args.database) if args.database else None,
                               'unique_compositions': len(known), 'scope': 'supplied_database_only'}
    result['seed_source_verification'] = 'matched_supplied_database' if args.database else 'caller_declared_not_independently_verified'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(f"{len(result['candidates'])} chemical-rule candidates; {len(result['rejected'])} rejected; no performance predictions")


if __name__ == '__main__':
    main()
