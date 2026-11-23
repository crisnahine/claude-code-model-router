from operator import itemgetter

from shop import events


def revenue(log):
    charges = sum(map(itemgetter("amount_cents"), (events.to_cents(e) for e in log if e["kind"] == "charge")))
    refunds = sum(map(itemgetter("amount_cents"), (events.to_cents(e) for e in log if e["kind"] == "refund")))
    return charges - refunds
