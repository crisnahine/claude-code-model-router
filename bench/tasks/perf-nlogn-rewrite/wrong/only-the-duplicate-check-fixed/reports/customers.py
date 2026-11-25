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
    spelling = {}
    for sale in sales:
        spelling.setdefault(sale.customer_key, sale.customer)
    totals = []
    for key in sorted(spelling, key=lambda k: names.sort_key(spelling[k])):
        theirs = [sale for sale in sales if sale.customer_key == key]
        totals.append(Total(key, spelling[key], sum(s.cents for s in theirs), len(theirs),
                            frozenset(s.region for s in theirs)))
    return totals


def by_product(sales):
    """One Total per product, biggest first, then in name order. A product is shown under its first spelling."""
    spelling, cents, count = {}, {}, {}
    for sale in sales:
        spelling.setdefault(sale.product_key, sale.product)
        cents[sale.product_key] = cents.get(sale.product_key, 0) + sale.cents
        count[sale.product_key] = count.get(sale.product_key, 0) + 1
    totals = [Total(k, spelling[k], cents[k], count[k], frozenset()) for k in spelling]
    return sorted(totals, key=lambda t: (-t.cents, names.sort_key(t.name)))
