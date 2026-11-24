"""How cache keys and tags are spelled.

A key names one cached value. A tag names one thing a value was built from (a facet of a sku, or a category), so
a write can drop every value built from what it changed.
"""

NAMESPACE = "shop"
VERSION = 3  # raise to orphan every key after a change to what a cached value holds


def key(kind, ident):
    return f"{NAMESPACE}:v{VERSION}:{kind}:{ident}"


def tag(facet, ident):
    return f"{facet}:{ident}"


def normalize_sku(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"not a sku: {raw!r}")
    return raw.strip().upper()
