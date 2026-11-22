from cache import store

PREFIX = "price:"


def sweep(keep):
    """Drop cached prices of products not in keep."""
    for key in store.keys():
        if key.startswith(PREFIX) and key[len(PREFIX):] not in keep:
            store.delete(key)
