"""The admin console's actions. Each action is one unit of work, so a form either saves whole or not at all."""


class Admin:
    def __init__(self, app):
        self.app = app

    def save_product(self, sku, name=None, price=None, category=None):
        """The product form's Save button. The price is written first because the pricing service validates it, and
        a bad price should stop the form before anything else is written."""
        with self.app.unit() as uow:
            if price is not None:
                self.app.pricing.set_price(uow, sku, price, reason="admin edit")
            if name is not None:
                self.app.catalog.rename(uow, sku, name)
            if category is not None:
                self.app.catalog.move(uow, sku, category)

    def add_product(self, sku, name, category, price, units=0):
        with self.app.unit() as uow:
            self.app.catalog.add(uow, sku, name, category)
            self.app.pricing.add(uow, sku, price)
            self.app.inventory.add(uow, sku, units)

    def restock(self, sku, units):
        with self.app.unit() as uow:
            self.app.inventory.adjust(uow, sku, units)

    def write_off(self, sku, units):
        with self.app.unit() as uow:
            self.app.inventory.adjust(uow, sku, -units)

    def bulk_reprice(self, prices, reason="bulk reprice"):
        """{sku: cents, ...} in one unit: all of them change or none does."""
        with self.app.unit() as uow:
            for sku, cents in sorted(prices.items()):
                self.app.pricing.set_price(uow, sku, cents, reason=reason)
