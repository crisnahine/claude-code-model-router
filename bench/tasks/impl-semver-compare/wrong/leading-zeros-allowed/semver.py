"""Semantic Versioning 2.0.0 precedence, and the highest version that satisfies a range."""
import re

_ID = r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = re.compile(rf"(\d+)\.(\d+)\.(\d+)(?:-({_ID}(?:\.{_ID})*))?"
                      r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
_COMPARATOR = re.compile(r"(<=|>=|<|>|=|\^)?(.*)")


def _parse(version):
    if not isinstance(version, str):
        raise ValueError(version)
    m = _VERSION.fullmatch(version)
    if not m:
        raise ValueError(version)
    return tuple(int(p) for p in m.groups()[:3]), (tuple(m.group(4).split(".")) if m.group(4) else None)


def _identifier(a, b):
    if a.isdigit() and b.isdigit():
        return (int(a) > int(b)) - (int(a) < int(b))
    if a.isdigit() or b.isdigit():
        return -1 if a.isdigit() else 1
    return (a > b) - (a < b)


def _compare(pa, pb):
    (core_a, pre_a), (core_b, pre_b) = pa, pb
    if core_a != core_b:
        return 1 if core_a > core_b else -1
    if pre_a is None or pre_b is None:
        return (pre_a is None) - (pre_b is None)
    for x, y in zip(pre_a, pre_b):
        result = _identifier(x, y)
        if result:
            return result
    return (len(pre_a) > len(pre_b)) - (len(pre_a) < len(pre_b))


def compare(a, b):
    """Compare two Semantic Versioning 2.0.0 version strings by precedence (-1, 0 or 1); ValueError if invalid."""
    return _compare(_parse(a), _parse(b))


def _comparators(token):
    op, rest = _COMPARATOR.fullmatch(token).groups()
    version = _parse(rest)
    if op != "^":
        return [(op or "=", version)]
    (major, minor, patch), _ = version
    upper = (major + 1, 0, 0) if major else (0, minor + 1, 0) if minor else (0, 0, patch + 1)
    return [(">=", version), ("<", (upper, None))]


_TESTS = {"=": lambda c: c == 0, "<": lambda c: c < 0, "<=": lambda c: c <= 0, ">": lambda c: c > 0,
          ">=": lambda c: c >= 0}


def _range(spec):
    if not isinstance(spec, str):
        raise ValueError(spec)
    sets = []
    for part in spec.split("||"):
        tokens = part.split()
        if not tokens:
            raise ValueError(spec)
        sets.append([c for token in tokens for c in _comparators(token)])
    return sets


def _satisfies(version, comparators):
    if not all(_TESTS[op](_compare(version, bound)) for op, bound in comparators):
        return False
    return version[1] is None or any(bound[1] is not None and bound[0] == version[0] for _, bound in comparators)


def max_satisfying(versions, spec):
    """The highest-precedence version in versions that satisfies the range spec, or None."""
    sets = _range(spec)
    best = None
    for text in versions:
        try:
            version = _parse(text)
        except ValueError:
            continue
        if any(_satisfies(version, s) for s in sets) and (best is None or _compare(version, best[1]) > 0):
            best = (text, version)
    return best[0] if best else None
