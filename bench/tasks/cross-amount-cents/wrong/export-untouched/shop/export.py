HEADER = "kind,order_id,amount"


def _row(kind, order_id, amount):
    return f"{kind},{order_id},{amount}"


def csv_rows(log):
    return [HEADER] + [_row(**e) for e in log]
