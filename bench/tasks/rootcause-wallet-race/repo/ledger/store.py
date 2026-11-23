"""The store every payout worker process shares; a wallet's balance is whole cents under the wallet's name.

    get(key) -> int | None                    the value, None when the key is absent
    put(key, value)                           set the value
    compare_and_put(key, expected, value)     set it only if it still holds expected (None: the key is absent);
                                              True when it was set
    lock(key)                                 a context manager that holds the key's lock across every worker
                                              process; not reentrant: taking a key you already hold waits forever

This in-memory Store is for one process. A deployment backs the same interface with a shared service.
"""
import contextlib
import threading


class Store:
    def __init__(self):
        self._data = {}
        self._mutex = threading.Lock()
        self._locks = {}

    def get(self, key):
        with self._mutex:
            return self._data.get(key)

    def put(self, key, value):
        with self._mutex:
            self._data[key] = value

    def compare_and_put(self, key, expected, value):
        with self._mutex:
            if self._data.get(key) != expected:
                return False
            self._data[key] = value
            return True

    @contextlib.contextmanager
    def lock(self, key):
        with self._mutex:
            held = self._locks.setdefault(key, threading.Lock())
        with held:
            yield
