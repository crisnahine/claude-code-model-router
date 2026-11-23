PRICES = {}


def set_price(product_id, cents):
    PRICES[product_id] = cents


def lookup(product_id):
    return PRICES[product_id]


def remove(product_id):
    PRICES.pop(product_id, None)
