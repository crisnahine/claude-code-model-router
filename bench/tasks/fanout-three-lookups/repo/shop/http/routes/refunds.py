from shop.orders import models, refund


def refund_order(request):
    order = models.find(int(request.params["id"]))
    if order is None or order.customer != request.user:
        return 404, {"error": "not found"}
    cents = int(request.body.get("cents", order.total_cents - order.refunded_cents))
    try:
        refund.refund(order, cents)
    except refund.RefundError as e:
        return 422, {"error": str(e)}
    return 200, order.as_dict()
