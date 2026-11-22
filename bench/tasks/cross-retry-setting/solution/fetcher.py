import config


def fetch(url, send):
    """Call send(url) until it returns a response, `retries` attempts at most."""
    for _ in range(config.load()["retries"]):
        response = send(url)
        if response is not None:
            return response
    return None
