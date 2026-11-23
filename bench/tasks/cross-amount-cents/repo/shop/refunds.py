def refundable(log, order_id):
    mine = [e for e in log if e["order_id"] == order_id]
    charged = sum(e["amount"] for e in mine if e["kind"] == "charge")
    refunded = sum(e["amount"] for e in mine if e["kind"] == "refund")
    return max(0.0, round(charged - refunded, 2))


def can_refund(log, order_id, amount):
    return 0 < amount <= refundable(log, order_id)
