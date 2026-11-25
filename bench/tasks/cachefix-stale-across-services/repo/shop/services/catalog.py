"""The catalog service: each product's name and category, and each category's listing."""
from ..cache import keys
from ..cache.changes import Change
from ..errors import InvalidRequest, NotFound

TABLE = "products"
MAX_NAME = 80


class Catalog:
    def __init__(self, db, cache):
        self.db = db
        self.cache = cache

    def product(self, sku):
        """{"sku", "name", "category"} for the sku."""
        sku = keys.normalize_sku(sku)
        return self.cache.get_or_load(keys.key("product", sku), lambda: self._load(sku), [keys.tag("product", sku)])

    def listing(self, category):
        """The category's products as [sku, name] pairs, by sku."""
        category = _clean_category(category)
        return self.cache.get_or_load(keys.key("listing", category), lambda: self._load_listing(category),
                                      [keys.tag("category", category)])

    def categories(self):
        return sorted({row["category"] for row in self.db.scan(TABLE)})

    def exists(self, sku):
        return self.db.get(TABLE, keys.normalize_sku(sku)) is not None

    def add(self, uow, sku, name, category):
        sku = keys.normalize_sku(sku)
        if uow.tx.get(TABLE, sku) is not None:
            raise InvalidRequest(f"{sku} already exists")
        category = _clean_category(category)
        uow.tx.put(TABLE, sku, {"sku": sku, "name": _clean_name(name), "category": category})
        uow.record(Change(sku, frozenset({"product"}), frozenset({keys.tag("category", category)})))

    def rename(self, uow, sku, name):
        sku = keys.normalize_sku(sku)
        row = self._row_for_write(uow, sku)
        name = _clean_name(name)
        if name == row["name"]:
            return
        uow.tx.put(TABLE, sku, dict(row, name=name))
        # The listing shows the name too.
        uow.record(Change(sku, frozenset({"product"}), frozenset({keys.tag("category", row["category"])})))

    def move(self, uow, sku, category):
        sku = keys.normalize_sku(sku)
        row = self._row_for_write(uow, sku)
        category = _clean_category(category)
        if category == row["category"]:
            return
        uow.tx.put(TABLE, sku, dict(row, category=category))
        uow.record(Change(sku, frozenset({"product"}),
                          frozenset({keys.tag("category", row["category"]), keys.tag("category", category)})))

    def _load(self, sku):
        row = self.db.get(TABLE, sku)
        if row is None:
            raise NotFound("product", sku)
        return {"sku": sku, "name": row["name"], "category": row["category"]}

    def _load_listing(self, category):
        return [[row["sku"], row["name"]] for row in self.db.scan(TABLE, lambda r: r["category"] == category)]

    def _row_for_write(self, uow, sku):
        row = uow.tx.get(TABLE, sku)
        if row is None:
            raise NotFound("product", sku)
        return row


def _clean_name(name):
    if not isinstance(name, str) or not name.strip():
        raise InvalidRequest("a product needs a name")
    name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise InvalidRequest(f"names are at most {MAX_NAME} characters")
    return name


def _clean_category(category):
    if not isinstance(category, str) or not category.strip():
        raise InvalidRequest("a product needs a category")
    return category.strip().lower()
