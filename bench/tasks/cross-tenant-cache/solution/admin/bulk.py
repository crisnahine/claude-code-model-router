from cache import keys, store
from catalog import db


def refresh_all(tenant_id, product_ids):
    """Reload the given products' prices into the cache."""
    for product_id in product_ids:
        store.set(keys.price_key(tenant_id, product_id), db.lookup(tenant_id, product_id))
