from cache import store


def cached_products():
    """Product ids whose price is in the cache, sorted."""
    return sorted(key.split(":", 1)[1] for key in store.keys() if key.startswith("price:"))
