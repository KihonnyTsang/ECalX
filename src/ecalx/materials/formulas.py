"""Conservative numeric formula reader, not a stability or synthesis validator."""
import re

from ecalx.domain.constraints import ValidationError

ELEMENTS = frozenset("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())
SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def formula_elements(formula):
    text = formula.translate(SUBSCRIPT).replace(" ", "")
    tokens = re.findall(r"[A-Z][a-z]?|\d+(?:\.\d+)?|[()]", text)
    if not tokens or "".join(tokens) != text:
        return None
    stack = [set()]
    can_multiply = False
    for token in tokens:
        if token == "(":
            stack.append(set())
            can_multiply = False
        elif token == ")":
            if len(stack) == 1 or not stack[-1]:
                return None
            group = stack.pop()
            stack[-1].update(group)
            can_multiply = True
        elif token[0].isdigit():
            if not can_multiply or float(token) <= 0:
                return None
            can_multiply = False
        elif token in ELEMENTS:
            stack[-1].add(token)
            can_multiply = True
        else:
            return None
    return stack[0] if len(stack) == 1 and stack[0] else None


def validate_symbols(symbols):
    unknown = set(symbols) - ELEMENTS
    if unknown:
        raise ValidationError("未知元素符号：" + ", ".join(sorted(unknown)))
    return tuple(sorted(set(symbols)))
