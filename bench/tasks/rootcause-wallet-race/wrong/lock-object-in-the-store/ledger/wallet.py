import contextlib
import threading


class InsufficientFunds(Exception):
    pass


def _lock(store, key):
    store.compare_and_put("lock:" + key, None, threading.Lock())
    return store.get("lock:" + key)


def balance(store, wallet):
    return store.get(wallet) or 0


def credit(store, wallet, cents):
    with _lock(store, wallet):
        store.put(wallet, balance(store, wallet) + cents)


def transfer(store, src, dst, cents):
    with contextlib.ExitStack() as s:
        for k in sorted({src, dst}):
            s.enter_context(_lock(store, k))
        available = balance(store, src)
        if available < cents:
            raise InsufficientFunds(src)
        if src != dst:
            store.put(src, available - cents)
            store.put(dst, balance(store, dst) + cents)
