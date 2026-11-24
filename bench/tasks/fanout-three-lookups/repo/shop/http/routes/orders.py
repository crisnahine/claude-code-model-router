from shop import pagination
from shop.orders import models


def _limit(request, listing):
    if "limit" in request.query:
        return pagination.clamp(int(request.query["limit"]))
    return pagination.default_for(listing)


def list_orders(request):
    orders = models.for_customer(request.user)
    limit = _limit(request, "orders")
    offset = int(request.query.get("offset", 0))
    return 200, {"orders": [o.as_dict() for o in pagination.page(orders, limit, offset)], "limit": limit}


def get_order(request):
    order = models.find(int(request.params["id"]))
    if order is None or order.customer != request.user:
        return 404, {"error": "not found"}
    return 200, order.as_dict()
