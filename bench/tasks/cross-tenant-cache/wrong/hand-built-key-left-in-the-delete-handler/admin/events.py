from cache import keys, store
from catalog import db


def on_price_changed(tenant_id, product_id):
    """Called after the admin service changes a price."""
    store.delete(keys.price_key(tenant_id, product_id))


def on_product_deleted(tenant_id, product_id):
    """Called after the admin service deletes a product."""
    db.remove(tenant_id, product_id)
    store.delete("price:" + product_id)
