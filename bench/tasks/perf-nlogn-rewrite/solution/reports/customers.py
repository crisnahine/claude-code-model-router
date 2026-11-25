"""Totals per customer, and per product, over a list of sales."""
from dataclasses import dataclass

from . import names


@dataclass(frozen=True)
class Total:
    key: str
    name: str
    cents: int
    count: int
    regions: frozenset


def by_customer(sales):
    """One Total per customer, in name order. A customer is shown under the spelling of their first sale."""
    spelling, cents, count, regions = {}, {}, {}, {}
    for sale in sales:
        key = sale.customer_key
        if key not in spelling:
            spelling[key], cents[key], count[key], regions[key] = sale.customer, 0, 0, set()
        cents[key] += sale.cents
        count[key] += 1
        regions[key].add(sale.region)
    return [Total(key, spelling[key], cents[key], count[key], frozenset(regions[key]))
            for key in sorted(spelling, key=lambda k: names.sort_key(spelling[k]))]


def by_product(sales):
    """One Total per product, biggest first, then in name order. A product is shown under its first spelling."""
    spelling, cents, count = {}, {}, {}
    for sale in sales:
        spelling.setdefault(sale.product_key, sale.product)
        cents[sale.product_key] = cents.get(sale.product_key, 0) + sale.cents
        count[sale.product_key] = count.get(sale.product_key, 0) + 1
    totals = [Total(k, spelling[k], cents[k], count[k], frozenset()) for k in spelling]
    return sorted(totals, key=lambda t: (-t.cents, names.sort_key(t.name)))
