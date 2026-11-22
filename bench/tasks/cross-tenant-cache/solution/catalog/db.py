PRICES = {}


def set_price(tenant_id, product_id, cents):
    PRICES[(tenant_id, product_id)] = cents


def lookup(tenant_id, product_id):
    return PRICES[(tenant_id, product_id)]


def remove(tenant_id, product_id):
    PRICES.pop((tenant_id, product_id), None)
