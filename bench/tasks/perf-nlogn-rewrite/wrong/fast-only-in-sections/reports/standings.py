"""Ranking totals. Equal totals share a rank and the next rank skips past them, so 1, 2, 2, 4."""


def competition_ranks(totals):
    """[(rank, total), ...] by cents, biggest first. Totals that share a rank keep the order they came in."""
    ranked = []
    for total in totals:
        rank = 1 + sum(1 for other in totals if other.cents > total.cents)
        ranked.append((rank, total))
    ranked.sort(key=lambda pair: pair[0])
    return ranked


def top(totals, places):
    """The ranked totals placed in the first `places` ranks, with every total tied for the last of them."""
    return [(rank, total) for rank, total in competition_ranks(totals) if rank <= places]
