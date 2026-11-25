"""The pricing service: each sku's list price in whole cents, and the history of every change to it."""
from ..cache import keys
from ..cache.changes import Change
from ..errors import InvalidRequest, NotFound

TABLE = "prices"
HISTORY = "price_history"
MAX_CENTS = 10_000_000


class Pricing:
    def __init__(self, db, cache):
        self.db = db
        self.cache = cache

    def price(self, sku):
        """The list price in cents, as the storefront shows it."""
        sku = keys.normalize_sku(sku)
        return self._load(sku)

    def current_price(self, sku):
        """The list price straight from the database, for the checks that must not trust the cache."""
        return self._load(keys.normalize_sku(sku))

    def set_price(self, uow, sku, cents, reason=""):
        sku = keys.normalize_sku(sku)
        _check_cents(cents)
        row = uow.tx.get(TABLE, sku)
        if row is None:
            raise NotFound("price", sku)
        if row["cents"] == cents:
            return
        uow.tx.put(TABLE, sku, dict(row, cents=cents))
        uow.tx.put(HISTORY, uow.tx.next_id(HISTORY),
                   {"sku": sku, "from": row["cents"], "to": cents, "reason": reason})
        uow.record(Change(sku, frozenset({"price"})))

    def add(self, uow, sku, cents):
        sku = keys.normalize_sku(sku)
        _check_cents(cents)
        if uow.tx.get(TABLE, sku) is not None:
            raise InvalidRequest(f"{sku} already has a price")
        uow.tx.put(TABLE, sku, {"sku": sku, "cents": cents})
        uow.record(Change(sku, frozenset({"price"})))

    def history(self, sku):
        sku = keys.normalize_sku(sku)
        return [[r["from"], r["to"], r["reason"]] for r in self.db.scan(HISTORY, lambda r: r["sku"] == sku)]

    def _load(self, sku):
        row = self.db.get(TABLE, sku)
        if row is None:
            raise NotFound("price", sku)
        return row["cents"]


def _check_cents(cents):
    if type(cents) is not int or not 0 < cents <= MAX_CENTS:
        raise InvalidRequest(f"a price is a whole number of cents from 1 to {MAX_CENTS}, not {cents!r}")
