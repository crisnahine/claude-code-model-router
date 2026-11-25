"""Keeps the busiest product pages in the cache, so the first customer after a change doesn't pay for the rebuild.

The job runs every minute on each app server. It only reads, so it can never make a cached value stale: a value it
loads is whatever the database holds at that moment.
"""
from collections import Counter

from . import keys

DEFAULT_TOP = 20


class Warmup:
    def __init__(self, cache, storefront):
        self.cache = cache
        self.storefront = storefront
        self.views = Counter()

    def saw(self, sku):
        """Count one page view; the API calls this for every product page it serves."""
        self.views[keys.normalize_sku(sku)] += 1

    def cold(self, top=DEFAULT_TOP):
        """The busiest skus whose page isn't cached right now."""
        busiest = [sku for sku, _ in self.views.most_common(top)]
        return [sku for sku in busiest if keys.key("page", sku) not in self.cache.backend]

    def run(self, top=DEFAULT_TOP):
        """Load every cold page among the busiest; return the skus loaded."""
        loaded = []
        for sku in self.cold(top):
            self.storefront.page(sku)
            loaded.append(sku)
        return loaded
