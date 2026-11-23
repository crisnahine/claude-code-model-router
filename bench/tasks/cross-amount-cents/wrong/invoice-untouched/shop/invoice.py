def lines(log, order_id):
    out = []
    for e in log:
        if e["order_id"] == order_id:
            kind = e["kind"]
            out.append(f"{kind} {e['amount']:.2f}")
    return out
