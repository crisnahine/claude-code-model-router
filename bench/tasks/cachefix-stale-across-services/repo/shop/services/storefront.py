"""The storefront: the product pages customers see, built from the catalog, pricing and inventory services."""
from ..cache import keys


class Storefront:
    def __init__(self, cache, catalog, pricing, inventory):
        self.cache = cache
        self.catalog = catalog
        self.pricing = pricing
        self.inventory = inventory

    def page(self, sku):
        """{"sku", "name", "category", "price", "stock", "in_stock"}, cached as a whole and tagged with every facet
        it shows, so a change to any of them drops it."""
        sku = keys.normalize_sku(sku)
        tags = [keys.tag(facet, sku) for facet in ("product", "price", "stock")]
        return self.cache.get_or_load(keys.key("page", sku), lambda: self._build(sku), tags)

    def category_page(self, category):
        """The category's products with their prices, as [sku, name, price] rows."""
        return [[sku, name, self.pricing.price(sku)] for sku, name in self.catalog.listing(category)]

    def _build(self, sku):
        product = self.catalog.product(sku)
        stock = self.inventory.stock(sku)
        return {"sku": sku, "name": product["name"], "category": product["category"],
                "price": self.pricing.price(sku), "stock": stock, "in_stock": stock > 0}
