from scoring import sort_key


def top(players, n=3):
    """The n players with the highest scores, best first."""
    return sorted(players, key=sort_key, reverse=True)[:n]
