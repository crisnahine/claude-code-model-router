from shop import events

HEADER = "kind,order_id,amount_cents"


def _row(kind, order_id, amount_cents):
    return f"{kind},{order_id},{amount_cents}"


def csv_rows(log):
    return [HEADER] + [_row(**events.to_cents(e)) for e in log]
