"""Wires the database, the shared cache and the services together."""
from . import seed as seeding
from .admin import Admin
from .cache import Cache, UnitOfWork
from .db import Database
from .services import catalog, inventory, orders, pricing
from .services.catalog import Catalog
from .services.inventory import Inventory
from .services.orders import Orders
from .services.pricing import Pricing
from .services.storefront import Storefront

TABLES = (catalog.TABLE, pricing.TABLE, pricing.HISTORY, inventory.TABLE, orders.TABLE)


class App:
    def __init__(self, db=None, cache=None):
        self.db = db if db is not None else Database()
        for table in TABLES:
            self.db.create_table(table)
        self.cache = cache if cache is not None else Cache()
        self.catalog = Catalog(self.db, self.cache)
        self.pricing = Pricing(self.db, self.cache)
        self.inventory = Inventory(self.db, self.cache)
        self.storefront = Storefront(self.cache, self.catalog, self.pricing, self.inventory)
        self.orders = Orders(self.db, self.storefront, self.pricing, self.inventory, self.unit)
        self.admin = Admin(self)

    def unit(self):
        """A new unit of work on this app's database and cache."""
        return UnitOfWork(self.db, self.cache)


def build_app(products=None):
    """An app with the seed products, or with products given as seed.PRODUCTS lists them."""
    app = App()
    seeding.seed(app, seeding.PRODUCTS if products is None else products)
    return app
