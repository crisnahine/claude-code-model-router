def allocate(total_cents, weights):
    """Split total_cents into integer shares in proportion to weights, adding up to total_cents.

    Each share starts as total_cents * weight / sum(weights), rounded down. The cents left over go one each to the
    shares with the largest remainders, ties going to the earlier share. A negative total, such as a refund, splits
    like its positive and is negated. weights is a non-empty list of non-negative ints with a positive sum; anything
    else raises ValueError.

    allocate(100, [1, 1, 1]) == [34, 33, 33]; allocate(-100, [1, 1, 1]) == [-34, -33, -33];
    allocate(5, [3, 0, 2]) == [3, 0, 2]; allocate(10, [2, 1]) == [7, 3].
    """
    total = sum(weights)
    return [round(total_cents * w / total) for w in weights]
