# TODO: move the tax table into config
TAX = {"US": 0.07, "DE": 0.19}


def total(lines, country):
    subtotal = sum(price * qty for price, qty in lines)
    # TODO: round per line, not on the total
    return round(subtotal * (1 + TAX.get(country, 0)), 2)
