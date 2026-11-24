from shop import events


def order_total(log, order_id):
    total = 0
    for e in log:
        cents = e["amount_cents"] if "amount_cents" in e else round(e["amount"] * 100)
        if e["order_id"] == order_id:
            total += cents if e["kind"] == "charge" else -cents
    return total
