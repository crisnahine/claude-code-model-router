from cache import store
from catalog import db


def on_price_changed(product_id):
    """Called after the admin service changes a price."""
    store.delete(f"price:{product_id}")


def on_product_deleted(product_id):
    """Called after the admin service deletes a product."""
    db.remove(product_id)
    store.delete("price:" + product_id)
