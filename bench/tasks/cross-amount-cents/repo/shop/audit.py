import operator

FIELDS = ("kind", "order_id", "amount")


def fingerprint(event):
    return "|".join(str(v) for v in operator.itemgetter(*FIELDS)(event))
