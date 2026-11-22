from cache import keys, store


def sweep(tenant_id, keep):
    """Drop this tenant's cached prices of products not in keep."""
    for key in store.keys():
        if keys.is_price_key(key, tenant_id) and key[2] not in keep:
            store.delete(key)
