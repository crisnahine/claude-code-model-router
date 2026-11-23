def allocate(total_cents, weights):
    s = sum(weights)
    shares = [total_cents * w // s for w in weights]
    shares[-1] += total_cents - sum(shares)
    return shares
