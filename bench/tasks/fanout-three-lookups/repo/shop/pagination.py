from shop import settings


def max_page_size():
    return settings.get_int("pagination", "max", 100)


def clamp(limit):
    return max(1, min(limit, max_page_size()))


def default_for(listing):
    """The page size a listing uses when the request gives no limit."""
    return clamp(settings.get_int("pagination", listing, settings.get_int("pagination", "default", 20)))


def page(items, limit, offset=0):
    return items[offset:offset + limit]
