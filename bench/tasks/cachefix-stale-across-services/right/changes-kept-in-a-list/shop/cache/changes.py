"""What a unit of work changed, as the cache needs to know it.

A change names a sku, the facets of it that changed (product, price, stock) and any tags its write touches beyond
them, such as the category listings a renamed or moved product shows up in. A unit keeps one change per sku, so a
unit that writes a sku many times still costs the cache one entry for it.
"""
from dataclasses import dataclass, replace

from . import keys

FACETS = ("product", "price", "stock")


@dataclass(frozen=True)
class Change:
    sku: str
    facets: frozenset
    extra_tags: frozenset = frozenset()
    seq: int = 0

    def __post_init__(self):
        unknown = set(self.facets) - set(FACETS)
        if unknown:
            raise ValueError(f"unknown facets {sorted(unknown)}")
        if not self.facets:
            raise ValueError("a change names at least one facet")

    def tags(self):
        return {keys.tag(facet, self.sku) for facet in self.facets} | set(self.extra_tags)

    def merge(self, later):
        """This change followed by a later one to the same sku. The later write holds the state that commits."""
        return replace(later, seq=max(self.seq, later.seq))


class ChangeSet:
    def __init__(self):
        self._changes = []

    def add(self, change):
        self._changes.append(replace(change, seq=len(self._changes) + 1))

    def get(self, sku):
        found = [c for c in self._changes if c.sku == sku]
        if not found:
            return None
        return Change(sku, frozenset().union(*(c.facets for c in found)),
                      frozenset().union(*(c.extra_tags for c in found)), found[-1].seq)

    def tags(self):
        """Every tag the unit's changes touch, sorted."""
        found = set()
        for change in self._changes:
            found |= change.tags()
        return sorted(found)

    def skus(self):
        return sorted({c.sku for c in self._changes})

    def __len__(self):
        return len(self.skus())
