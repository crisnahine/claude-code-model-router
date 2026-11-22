def price_key(tenant_id, product_id):
    return ("price", tenant_id, product_id)


def is_price_key(key, tenant_id):
    return isinstance(key, tuple) and len(key) == 3 and key[0] == "price" and key[1] == tenant_id
