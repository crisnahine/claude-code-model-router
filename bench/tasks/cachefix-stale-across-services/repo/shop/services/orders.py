"""The order service: quotes built from the product pages customers saw, and orders placed from a quote."""
from dataclasses import dataclass

from ..cache import keys
from ..errors import InvalidRequest, NotFound, PriceMismatch

TABLE = "orders"
MAX_LINES = 50


@dataclass(frozen=True)
class Line:
    sku: str
    quantity: int
    unit_price: int

    @property
    def amount(self):
        return self.quantity * self.unit_price


@dataclass(frozen=True)
class Quote:
    lines: tuple

    @property
    def total(self):
        return sum(line.amount for line in self.lines)


@dataclass(frozen=True)
class Order:
    id: int
    lines: tuple
    total: int


class Orders:
    def __init__(self, db, storefront, pricing, inventory, unit):
        self.db = db
        self.storefront = storefront
        self.pricing = pricing
        self.inventory = inventory
        self.unit = unit

    def quote(self, items):
        """A quote for [(sku, quantity), ...] at the prices the product pages show."""
        if not items or len(items) > MAX_LINES:
            raise InvalidRequest(f"a quote has 1 to {MAX_LINES} lines")
        lines = []
        for sku, quantity in items:
            if type(quantity) is not int or quantity <= 0:
                raise InvalidRequest(f"order at least one unit of {sku}")
            page = self.storefront.page(sku)
            lines.append(Line(page["sku"], quantity, page["price"]))
        return Quote(tuple(lines))

    def _verify(self, line):
        # Charge only the price the customer was shown: a quote is refused once the list price has moved.
        current = self.pricing.current_price(line.sku)
        if current != line.unit_price:
            raise PriceMismatch(line.sku, line.unit_price, current)

    def place_order(self, quote):
        """Take the stock and record the order; raise PriceMismatch for a stale quote, OutOfStock when short."""
        if not quote.lines:
            raise InvalidRequest("an empty quote")
        for line in quote.lines:
            self._verify(line)
        with self.unit() as uow:
            for line in quote.lines:
                self.inventory.reserve(uow, line.sku, line.quantity)
            order_id = uow.tx.next_id(TABLE)
            uow.tx.put(TABLE, order_id, {"id": order_id, "total": quote.total,
                                         "lines": [[l.sku, l.quantity, l.unit_price] for l in quote.lines]})
        return Order(order_id, quote.lines, quote.total)

    def order(self, order_id):
        row = self.db.get(TABLE, order_id)
        if row is None:
            raise NotFound("order", order_id)
        lines = tuple(Line(keys.normalize_sku(s), q, p) for s, q, p in row["lines"])
        return Order(row["id"], lines, row["total"])
