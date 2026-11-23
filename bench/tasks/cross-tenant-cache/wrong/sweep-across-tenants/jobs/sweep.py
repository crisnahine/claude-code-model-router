from cache import keys, store


def sweep(tenant_id, keep):
    for key in store.keys():
        if key[2] not in keep:
            store.delete(key)
