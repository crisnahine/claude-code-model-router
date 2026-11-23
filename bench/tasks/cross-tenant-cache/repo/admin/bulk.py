from cache import keys, store
from catalog import db


def refresh_all(product_ids):
    """Reload the given products' prices into the cache."""
    for product_id in product_ids:
        store.set(keys.price_key(product_id), db.lookup(product_id))
