from shop import events


def order_total(log, order_id):
    total = 0
    for e in map(events.to_cents, log):
        if e["order_id"] == order_id:
            total += e["amount_cents"] if e["kind"] == "charge" else -e["amount_cents"]
    return total
