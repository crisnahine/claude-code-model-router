from money import allocate


def per_person(total_cents, shares):
    """An invoice split between people by their shares, in cents: {name: cents}."""
    names = list(shares)
    return dict(zip(names, allocate(total_cents, [shares[n] for n in names])))
