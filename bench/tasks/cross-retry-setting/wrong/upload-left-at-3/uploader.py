def upload(path, put):
    """Call put(path) until it succeeds, 3 attempts at most."""
    attempts = 0
    while attempts < 3:
        if put(path):
            return True
        attempts += 1
    return False
