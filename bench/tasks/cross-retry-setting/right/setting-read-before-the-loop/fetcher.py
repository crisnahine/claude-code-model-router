import config


def fetch(url, send):
    """Call send(url) until it returns a response, `retries` attempts at most."""
    retries = config.load()["retries"]
    for _ in range(retries):
        response = send(url)
        if response is not None:
            return response
    return None
