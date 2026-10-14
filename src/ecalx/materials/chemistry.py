"""Exact numeric stoichiometry; no variable formulas, hydrates or inferred sites."""
import re
from math import lcm, gcd
from functools import reduce
from fractions import Fraction
from decimal import Decimal, localcontext

from ecalx.materials.formulas import ELEMENTS, SUBSCRIPT


def parse_formula(formula):
    text = formula.translate(SUBSCRIPT).replace(' ', '')
    if len(text) > 2048:
        raise ValueError('Formula too long')
    tokens = re.findall(r'[A-Z][a-z]?|(?:\d+(?:\.\d+)?|\.\d+)|[()]', text)
    if not tokens or ''.join(tokens) != text:
        raise ValueError('Only explicit numeric formulas and parentheses are supported')
    pos = 0

    def multiplier():
        nonlocal pos
        if pos < len(tokens) and (tokens[pos][0].isdigit() or tokens[pos][0] == '.'):
            value = Fraction(tokens[pos]); pos += 1
            if not value > 0:
                raise ValueError('Stoichiometry must be positive')
            return value
        return Fraction(1)

    def group(depth=0):
        nonlocal pos
        if depth > 16:
            raise ValueError('Formula nesting too deep')
        values = {}
        while pos < len(tokens) and tokens[pos] != ')':
            token = tokens[pos]; pos += 1
            if token == '(':
                inner = group(depth + 1)
                if not inner or pos == len(tokens) or tokens[pos] != ')':
                    raise ValueError('Unbalanced or empty group')
                pos += 1
                factor = multiplier()
                for element, amount in inner.items():
                    values[element] = values.get(element, 0) + amount * factor
            elif token in ELEMENTS:
                values[token] = values.get(token, 0) + multiplier()
            else:
                raise ValueError('Invalid element or stoichiometry: ' + token)
        return values

    result = group()
    if pos != len(tokens) or not result:
        raise ValueError('Unbalanced formula')
    return result


def composition_key(amounts):
    total = sum(amounts.values())
    if not total or any(v <= 0 for v in amounts.values()):
        raise ValueError('Invalid composition')
    return '|'.join(f'{e}:{float(v / total):.8f}' for e, v in sorted(amounts.items()))


def exact_key(amounts):
    total = sum(amounts.values())
    return '|'.join(f'{e}:{v / total}' for e, v in sorted(amounts.items()))


def display_formula(amounts):
    # Non-terminating rational coefficients use an exact integer empirical
    # formula. Do not round a valid arbitrary substitution to a different recipe.
    def terminating(n):
        d = n.denominator
        for prime in (2, 5):
            while d % prime == 0:
                d //= prime
        return d == 1
    if not all(terminating(n) for n in amounts.values()):
        scale = lcm(*(n.denominator for n in amounts.values()))
        integers = {e: int(n*scale) for e, n in amounts.items()}
        common = reduce(gcd, integers.values())
        amounts = {e: Fraction(n//common) for e, n in integers.items()}
    def coefficient(value):
        if value == 1:
            return ''
        # Do not emit
        # scientific notation (unsupported by our formula parser) or rounded floats.
        denominator = value.denominator
        twos = fives = 0
        while denominator % 2 == 0:
            denominator //= 2; twos += 1
        while denominator % 5 == 0:
            denominator //= 5; fives += 1
        if denominator != 1:
            raise ValueError('Non-terminating stoichiometry requires a decimal seed representation')
        with localcontext() as context:
            context.prec = len(str(abs(value.numerator))) + max(twos, fives) + 2
            text = format(Decimal(value.numerator)/Decimal(value.denominator), 'f')
            return text.rstrip('0').rstrip('.') if '.' in text else text
    return ''.join(e + coefficient(n)
                   for e, n in sorted(amounts.items(), key=lambda pair: (pair[0] == 'O', pair[0])))
