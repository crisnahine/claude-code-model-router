from shop import events


def lines(log, order_id):
    out = []
    for e in map(events.to_cents, log):
        if e["order_id"] == order_id:
            kind, cents = e["kind"], e["amount_cents"]
            out.append(f"{kind} {cents // 100}.{cents % 100:02d}")
    return out
