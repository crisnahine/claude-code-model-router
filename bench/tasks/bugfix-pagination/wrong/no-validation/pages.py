def page_slice(items, page, size):
    """Return page `page` of `items`, `size` items per page. Pages are numbered from 1."""
    start = (page - 1) * size
    return items[start:start + size]
