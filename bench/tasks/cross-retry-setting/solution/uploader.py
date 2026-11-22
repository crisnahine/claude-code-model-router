import config


def upload(path, put):
    """Call put(path) until it succeeds, `retries` attempts at most."""
    attempts = 0
    while attempts < config.load()["retries"]:
        if put(path):
            return True
        attempts += 1
    return False
