"""Semantic Versioning 2.0.0 precedence, and the highest version that satisfies a range."""


def compare(a, b):
    """Compare two Semantic Versioning 2.0.0 version strings by precedence.

    Return -1 if a has lower precedence than b, 0 if they have the same precedence, and 1 if higher.

    A version is MAJOR.MINOR.PATCH, optionally followed by -PRERELEASE and then +BUILD. MAJOR, MINOR and PATCH are
    non-negative integers without leading zeros. PRERELEASE and BUILD are dot-separated identifiers of ASCII letters,
    digits and hyphens; no identifier is empty, and a PRERELEASE identifier made only of digits has no leading zeros
    (BUILD identifiers may have them). No leading "v", no whitespace.

    Precedence: compare MAJOR, MINOR and PATCH numerically. A version with a PRERELEASE has lower precedence than the
    same version without one. Two PRERELEASEs compare identifier by identifier, left to right: identifiers of digits
    only compare numerically, others compare in ASCII order, and a digits-only identifier is lower than any other.
    If every identifier so far is equal, the PRERELEASE with more identifiers is higher. BUILD is ignored.

    Raise ValueError if either string is not a valid version.
    """
    raise NotImplementedError


def max_satisfying(versions, spec):
    """The version in versions with the highest precedence that satisfies the range spec, or None.

    A range is one or more comparator sets joined by "||"; a version satisfies the range if it satisfies any set.
    A set is one or more comparators separated by spaces; a version satisfies the set if it satisfies every
    comparator in it. A comparator is an operator (<, <=, >, >= or =) immediately followed by a full version, a full
    version alone (meaning =), or a caret range: ^1.2.3 means >=1.2.3 <2.0.0, ^0.2.3 means >=0.2.3 <0.3.0, and
    ^0.0.3 means >=0.0.3 <0.0.4. A caret version may carry a PRERELEASE: ^1.2.3-beta.2 means >=1.2.3-beta.2 <2.0.0.
    Any amount of whitespace may separate comparators and surround "||".

    A version with a PRERELEASE satisfies a set only if it satisfies every comparator in it and at least one
    comparator in that set (a caret counts as its lower bound) names a version with the same MAJOR.MINOR.PATCH and a
    PRERELEASE. So 1.2.4-rc.1 doesn't satisfy ^1.2.3, and 1.2.3-rc.2 satisfies ^1.2.3-rc.1.

    Strings in versions that aren't valid versions are skipped. Of satisfying versions with equal precedence, the
    first in the list wins. Raise ValueError if spec isn't a valid range: empty, an empty set, an unknown operator,
    or a comparator whose version isn't valid.
    """
    raise NotImplementedError
