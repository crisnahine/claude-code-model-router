from rates import rate_table


def price_list(region):
    """Full prices in cents, by SKU."""
    return {row["sku"]: row["price"] for row in rate_table(region)}


def sale_prices(region, percent_off):
    """Sale prices in cents, by SKU: percent_off off each full price, rounded down."""
    return {row["sku"]: row["price"] * (100 - percent_off) // 100 for row in rate_table(region)}
