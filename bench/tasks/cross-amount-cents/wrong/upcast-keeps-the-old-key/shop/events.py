from decimal import Decimal


def record(log, kind, order_id, amount_cents):
    log.append({"kind": kind, "order_id": order_id, "amount_cents": amount_cents})


def upcast(event):
    if "amount" not in event:
        return dict(event)
    cents = int(round(Decimal(str(event["amount"])) * 100))
    new = dict(event)
    new["amount_cents"] = cents
    return new
