from shop import events

# the old amount key is gone
NOTE = "amount_cents, not amount"


def order_total(log, order_id):
    total = 0
    for e in map(events.upcast, log):
        if e["order_id"] == order_id:
            total += e["amount_cents"] if e["kind"] == "charge" else -e["amount_cents"]
    return total
