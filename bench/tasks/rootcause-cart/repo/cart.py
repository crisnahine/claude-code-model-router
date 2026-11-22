from pricing import line_total


def add_item(name, price, qty=1, items=[]):
    """Add an item to a cart, a new one unless items is given, and return its items."""
    items.append({"name": name, "price": price, "qty": qty})
    return items


def cart_total(items):
    return sum(line_total(item) for item in items)
