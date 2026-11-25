class ShopError(Exception):
    """Base for the errors the API turns into a 4xx answer."""

    status = 400


class NotFound(ShopError):
    status = 404

    def __init__(self, kind, ident):
        super().__init__(f"no {kind} {ident}")
        self.kind = kind
        self.ident = ident


class InvalidRequest(ShopError):
    status = 422


class OutOfStock(ShopError):
    status = 409

    def __init__(self, sku, wanted, held):
        super().__init__(f"{sku}: {wanted} wanted, {held} in stock")
        self.sku = sku
        self.wanted = wanted
        self.held = held


class PriceMismatch(ShopError):
    """A quote no longer matches the list price, so the order would charge something the customer didn't see."""

    status = 409

    def __init__(self, sku, quoted, current):
        super().__init__(f"{sku} was quoted at {quoted} but costs {current}")
        self.sku = sku
        self.quoted = quoted
        self.current = current
