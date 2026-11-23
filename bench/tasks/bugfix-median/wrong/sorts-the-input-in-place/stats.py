def median(values):
    """Return the median of a non-empty sequence of numbers."""
    if not values:
        raise ValueError("median of an empty sequence")
    values.sort()
    ordered = values
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
