class InsufficientFunds(Exception):
    pass


def balance(store, wallet):
    """The wallet's balance in cents, 0 for a wallet the store hasn't seen."""
    return store.get(wallet) or 0


def credit(store, wallet, cents):
    """Add cents to the wallet."""
    store.put(wallet, balance(store, wallet) + cents)


def transfer(store, src, dst, cents):
    """Move cents from src to dst; raise InsufficientFunds when src holds less."""
    with store.lock(src):
        with store.lock(dst):
            available = balance(store, src)
            if available < cents:
                raise InsufficientFunds(src)
            store.put(src, available - cents)
            store.put(dst, balance(store, dst) + cents)
