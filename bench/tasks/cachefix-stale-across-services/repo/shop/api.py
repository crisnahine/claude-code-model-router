"""The HTTP handlers, framework-free: each takes the app and a decoded JSON body and returns (status, body)."""
import logging

from .errors import InvalidRequest, ShopError

log = logging.getLogger("shop.api")


def _items(body):
    items = body.get("items")
    if not isinstance(items, list):
        raise InvalidRequest("items is a list of [sku, quantity] pairs")
    try:
        return [(str(sku), int(quantity)) for sku, quantity in items]
    except (TypeError, ValueError):
        raise InvalidRequest("items is a list of [sku, quantity] pairs") from None


def _answer(fn):
    def handler(app, body=None):
        try:
            return 200, fn(app, body or {})
        except ShopError as e:
            log.warning("%s: %s", type(e).__name__, e)
            return e.status, {"error": type(e).__name__, "message": str(e)}
    handler.__name__ = fn.__name__
    return handler


@_answer
def handle_page(app, body):
    return app.storefront.page(body.get("sku", ""))


@_answer
def handle_category(app, body):
    return {"products": app.storefront.category_page(body.get("category", ""))}


@_answer
def handle_quote(app, body):
    quote = app.orders.quote(_items(body))
    return {"lines": [[l.sku, l.quantity, l.unit_price] for l in quote.lines], "total": quote.total}


@_answer
def handle_checkout(app, body):
    quote = app.orders.quote(_items(body))
    order = app.orders.place_order(quote)
    return {"order": order.id, "total": order.total}


@_answer
def handle_admin_save(app, body):
    sku = body.get("sku", "")
    app.admin.save_product(sku, name=body.get("name"), price=body.get("price"), category=body.get("category"))
    return app.storefront.page(sku)


@_answer
def handle_admin_restock(app, body):
    app.admin.restock(body.get("sku", ""), body.get("units"))
    return app.storefront.page(body.get("sku", ""))
