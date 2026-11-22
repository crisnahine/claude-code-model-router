from cache import keys, store


def cached_products(tenant_id):
    """Product ids whose price is in the cache for this tenant, sorted."""
    return sorted(key[2] for key in store.keys() if keys.is_price_key(key, tenant_id))
