from operator import itemgetter


def revenue(log):
    charges = sum(map(itemgetter("amount"), (e for e in log if e["kind"] == "charge")))
    refunds = sum(map(itemgetter("amount"), (e for e in log if e["kind"] == "refund")))
    return round(charges - refunds, 2)
