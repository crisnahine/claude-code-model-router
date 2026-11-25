"""A unit of work: one database transaction, and the cached values it makes stale."""
from .changes import ChangeSet


class UnitOfWork:
    """Use as a context manager. Writes go through `tx`, and each write says what it changed with `record`.

    On a clean exit the transaction commits, then every cached value carrying a tag the changes touch is dropped.
    On an error the transaction rolls back and the cache is left as it was.

    Dropping waits for the commit: a read during the unit sees the committed rows, so a value it caches is the old
    one, and dropping before the commit would let that read put the old value back.
    """

    def __init__(self, db, cache):
        self.db = db
        self.cache = cache
        self.tx = None
        self.changes = ChangeSet()
        self.dropped = []

    def __enter__(self):
        self.tx = self.db.begin()
        return self

    def record(self, change):
        if self.tx is None or self.tx.done:
            raise RuntimeError("record() outside an open unit of work")
        self.changes.add(change)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.tx.rollback()
            return False
        self.tx.commit()
        self.dropped = self.cache.backend.keys()
        self.cache.clear()
        return False
