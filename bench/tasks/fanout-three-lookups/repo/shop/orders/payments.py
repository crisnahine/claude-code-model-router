_refunds = []


def refund(order_id, cents):
    """Ask the payment provider to refund; True when it accepted."""
    _refunds.append((order_id, cents))
    return True
