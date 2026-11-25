from shop import events
from shop.orders import payments


class RefundError(Exception):
    pass


def refund(order, cents):
    if cents <= 0:
        raise RefundError("nothing to refund")
    if order.refunded_cents + cents > order.total_cents:
        raise RefundError("more than was paid")
    if not payments.refund(order.id, cents):
        events.emit("order.refund_failed", order=order, cents=cents)
        raise RefundError("the payment provider refused the refund")
    order.refunded_cents += cents
    order.status = "refunded" if order.refunded_cents == order.total_cents else "partly refunded"
    events.emit("order.refunded", order=order, cents=cents)
