def format_price(cents):
    """An amount in cents as "$12.34"."""
    return f"${cents // 100}.{cents % 100:02d}"
