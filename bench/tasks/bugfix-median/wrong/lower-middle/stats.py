def median(values):
    """Return the median of a non-empty sequence of numbers."""
    if not values:
        raise ValueError("median of an empty sequence")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return ordered[middle - 1]
