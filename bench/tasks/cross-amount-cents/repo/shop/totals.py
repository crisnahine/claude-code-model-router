def order_total(log, order_id):
    total = 0.0
    for e in log:
        if e["order_id"] == order_id:
            total += e["amount"] if e["kind"] == "charge" else -e["amount"]
    return round(total, 2)
