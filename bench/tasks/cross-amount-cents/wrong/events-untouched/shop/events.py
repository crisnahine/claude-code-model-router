def record(log, kind, order_id, amount):
    log.append({"kind": kind, "order_id": order_id, "amount": amount})
