def fetch(url, send):
    """Call send(url) until it returns a response, 3 attempts at most."""
    for _ in range(3):
        response = send(url)
        if response is not None:
            return response
    return None
