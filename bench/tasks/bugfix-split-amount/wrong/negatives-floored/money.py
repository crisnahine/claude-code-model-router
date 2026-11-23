def allocate(total_cents, weights):
    """Split total_cents into integer shares in proportion to weights, adding up to total_cents.

    Each share starts as total_cents * weight / sum(weights), rounded down. The cents left over go one each to the
    shares with the largest remainders, ties going to the earlier share. A negative total, such as a refund, splits
    like its positive and is negated. weights is a non-empty list of non-negative ints with a positive sum; anything
    else raises ValueError.

    allocate(100, [1, 1, 1]) == [34, 33, 33]; allocate(-100, [1, 1, 1]) == [-34, -33, -33];
    allocate(5, [3, 0, 2]) == [3, 0, 2]; allocate(10, [2, 1]) == [7, 3].
    """
    if (not isinstance(weights, list) or not weights or any(type(w) is not int or w < 0 for w in weights)
            or sum(weights) <= 0):
        raise ValueError(f"weights must be non-negative ints with a positive sum, not {weights!r}")
    total, sign = sum(weights), 1
    parts = [divmod(total_cents * w, total) for w in weights]
    shares = [q for q, _ in parts]
    for i in sorted(range(len(parts)), key=lambda i: (-parts[i][1], i))[:total_cents - sum(shares)]:
        shares[i] += 1
    return [sign * s for s in shares]
