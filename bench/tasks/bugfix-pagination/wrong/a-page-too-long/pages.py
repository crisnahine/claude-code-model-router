def page_slice(items, page, size):
    """Return page `page` of `items`, `size` items per page. Pages are numbered from 1."""
    if page < 1 or size < 1:
        raise ValueError("page and size start at 1")
    start = (page - 1) * size
    return items[start:start + size + 1]
