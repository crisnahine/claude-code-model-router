"""An in-memory database: named tables of rows keyed by id, and one transaction at a time.

Reads through `Database.get` and `Database.scan` see committed rows only. Writes go through a `Transaction` and
land together on commit. A transaction reads its own writes with `Transaction.get`.

Every committed read is counted per table in `Database.reads`, which is how the tests see what the cache saved.
"""
import copy
import threading
from collections import Counter

_DELETED = object()


class TransactionError(Exception):
    pass


class Database:
    def __init__(self):
        self._tables = {}
        self._lock = threading.RLock()
        self._open = None
        self.reads = Counter()
        self.commits = 0

    def create_table(self, name):
        with self._lock:
            self._tables.setdefault(name, {})

    def tables(self):
        return sorted(self._tables)

    def get(self, table, key):
        """The committed row, or None."""
        with self._lock:
            self.reads[table] += 1
            return copy.deepcopy(self._table(table).get(key))

    def scan(self, table, where=None):
        """Every committed row for which where(row) is true, by key."""
        with self._lock:
            self.reads[table] += 1
            rows = [copy.deepcopy(row) for _, row in sorted(self._table(table).items())]
        return [row for row in rows if where is None or where(row)]

    def count(self, table):
        with self._lock:
            return len(self._table(table))

    def begin(self):
        with self._lock:
            if self._open is not None:
                raise TransactionError("a transaction is already open")
            self._open = Transaction(self)
            return self._open

    def in_transaction(self):
        return self._open is not None

    def _table(self, name):
        try:
            return self._tables[name]
        except KeyError:
            raise TransactionError(f"no table {name!r}") from None

    def _finish(self, tx, writes):
        with self._lock:
            if self._open is not tx:
                raise TransactionError("that transaction isn't open")
            for (table, key), row in writes.items():
                rows = self._table(table)
                if row is _DELETED:
                    rows.pop(key, None)
                else:
                    rows[key] = row
            self._open = None
            if writes:
                self.commits += 1


class Transaction:
    def __init__(self, db):
        self._db = db
        self._writes = {}
        self.done = False

    def get(self, table, key):
        """The row as this transaction sees it: its own write if it made one, else the committed row."""
        self._check()
        if (table, key) in self._writes:
            row = self._writes[(table, key)]
            return None if row is _DELETED else copy.deepcopy(row)
        with self._db._lock:
            return copy.deepcopy(self._db._table(table).get(key))

    def put(self, table, key, row):
        self._check()
        self._db._table(table)
        self._writes[(table, key)] = copy.deepcopy(row)

    def delete(self, table, key):
        self._check()
        self._writes[(table, key)] = _DELETED

    def next_id(self, table):
        """One more than the highest integer key, counting this transaction's writes."""
        self._check()
        with self._db._lock:
            keys = [k for k in self._db._table(table) if isinstance(k, int)]
        keys += [k for (t, k), row in self._writes.items() if t == table and isinstance(k, int) and row is not _DELETED]
        return max(keys, default=0) + 1

    def commit(self):
        self._check()
        self.done = True
        self._db._finish(self, self._writes)

    def rollback(self):
        self._check()
        self.done = True
        self._db._finish(self, {})

    def _check(self):
        if self.done:
            raise TransactionError("the transaction has ended")
