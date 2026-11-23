from cache import keys, store
from catalog import db


def get_price(product_id):
    key = keys.price_key(product_id)
    cached = store.get(key)
    if cached is not None:
        return cached
    cents = db.lookup(product_id)
    store.set(key, cents)
    return cents
