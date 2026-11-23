from cache import store


def sweep(tenant_id, keep):
    prefix = f"price:{tenant_id}:"
    for key in store.keys():
        if key.startswith(prefix) and key[len(prefix):] not in keep:
            store.delete(key)
