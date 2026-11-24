from shop import pagination
from shop.orders import models


def list_customers(request):
    if not getattr(request.user, "is_staff", False):
        return 403, {"error": "staff only"}
    limit = pagination.default_for("customers")
    if "limit" in request.query:
        limit = pagination.clamp(int(request.query["limit"]))
    customers = models.customers()
    return 200, {"customers": [c.as_dict() for c in pagination.page(customers, limit)], "limit": limit}


def get_customer(request):
    customer = models.find_customer(int(request.params["id"]))
    if customer is None:
        return 404, {"error": "not found"}
    return 200, customer.as_dict()
