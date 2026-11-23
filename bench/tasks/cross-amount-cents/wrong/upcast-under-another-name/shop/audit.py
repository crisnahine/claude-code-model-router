import operator

from shop import events

FIELDS = ("kind", "order_id", "amount_cents")


def fingerprint(event):
    return "|".join(str(v) for v in operator.itemgetter(*FIELDS)(events.to_cents(event)))
