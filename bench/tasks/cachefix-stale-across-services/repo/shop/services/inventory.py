"""The inventory service: units in stock per sku."""
from ..cache import keys
from ..cache.changes import Change
from ..errors import InvalidRequest, NotFound, OutOfStock

TABLE = "stock"


class Inventory:
    def __init__(self, db, cache):
        self.db = db
        self.cache = cache

    def stock(self, sku):
        """Units in stock, as the storefront shows it."""
        sku = keys.normalize_sku(sku)
        return self.cache.get_or_load(keys.key("stock", sku), lambda: self._load(sku), [keys.tag("stock", sku)])

    def current_stock(self, sku):
        return self._load(keys.normalize_sku(sku))

    def adjust(self, uow, sku, delta):
        """Add delta units (negative to take them away); raise OutOfStock rather than go below zero."""
        sku = keys.normalize_sku(sku)
        if type(delta) is not int:
            raise InvalidRequest(f"a stock change is a whole number of units, not {delta!r}")
        if delta == 0:
            return
        row = uow.tx.get(TABLE, sku)
        if row is None:
            raise NotFound("stock", sku)
        units = row["units"] + delta
        if units < 0:
            raise OutOfStock(sku, -delta, row["units"])
        uow.tx.put(TABLE, sku, dict(row, units=units))
        uow.record(Change(sku, frozenset({"stock"})))

    def reserve(self, uow, sku, quantity):
        if type(quantity) is not int or quantity <= 0:
            raise InvalidRequest(f"order at least one unit, not {quantity!r}")
        self.adjust(uow, sku, -quantity)

    def add(self, uow, sku, units):
        sku = keys.normalize_sku(sku)
        if type(units) is not int or units < 0:
            raise InvalidRequest(f"stock is a whole number of units, not {units!r}")
        uow.tx.put(TABLE, sku, {"sku": sku, "units": units})
        uow.record(Change(sku, frozenset({"stock"})))

    def _load(self, sku):
        row = self.db.get(TABLE, sku)
        if row is None:
            raise NotFound("stock", sku)
        return row["units"]
