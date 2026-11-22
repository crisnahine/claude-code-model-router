def median(values):
    """Return the median of a non-empty sequence of numbers."""
    if not values:
        raise ValueError("median of an empty sequence")
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
