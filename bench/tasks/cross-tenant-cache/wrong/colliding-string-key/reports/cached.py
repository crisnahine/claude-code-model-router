from cache import store


def cached_products(tenant_id):
    prefix = f"price:{tenant_id}:"
    return sorted(k[len(prefix):] for k in store.keys() if k.startswith(prefix))
