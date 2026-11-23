from shop import events


def refundable(log, order_id):
    mine = [events.upcast(e) for e in log if e["order_id"] == order_id]
    charged = sum(e["amount_cents"] for e in mine if e["kind"] == "charge")
    refunded = sum(e["amount_cents"] for e in mine if e["kind"] == "refund")
    return max(0, charged - refunded)


def can_refund(log, order_id, amount_cents):
    return 0 < amount_cents <= refundable(log, order_id)
