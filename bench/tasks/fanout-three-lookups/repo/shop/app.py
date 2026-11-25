from shop import notify  # noqa: F401
from shop import webhooks  # noqa: F401
from shop.http.router import Router
from shop.http.routes import customers, orders, refunds


def build():
    router = Router()
    router.add("GET", "/orders", orders.list_orders)
    router.add("GET", "/orders/<id>", orders.get_order)
    router.add("POST", "/orders/<id>/refund", refunds.refund_order)
    router.add("GET", "/customers", customers.list_customers)
    router.add("GET", "/customers/<id>", customers.get_customer)
    return router
