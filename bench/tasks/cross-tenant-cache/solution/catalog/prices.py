from cache import keys, store
from catalog import db


def get_price(tenant_id, product_id):
    key = keys.price_key(tenant_id, product_id)
    cached = store.get(key)
    if cached is not None:
        return cached
    cents = db.lookup(tenant_id, product_id)
    store.set(key, cents)
    return cents
