from shop import events


def order_total(log, order_id):
    total = 0
    for e in map(events.upcast, log):
        if e["order_id"] == order_id:
            cents = int(e["amount_cents"] / 100 * 100)
            total += cents if e["kind"] == "charge" else -cents
    return total
