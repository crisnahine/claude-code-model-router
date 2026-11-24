"""Ranking totals. Equal totals share a rank and the next rank skips past them, so 1, 2, 2, 4."""


def competition_ranks(totals):
    """[(rank, total), ...] by cents, biggest first. Totals that share a rank keep the order they came in."""
    return list(enumerate(sorted(totals, key=lambda t: -t.cents), 1))


def top(totals, places):
    """The ranked totals placed in the first `places` ranks, with every total tied for the last of them."""
    return [(rank, total) for rank, total in competition_ranks(totals) if rank <= places]
