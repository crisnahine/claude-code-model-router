import os

DEFAULT_RETRIES = 3
DEFAULT_TIMEOUT = 10.0


def read_retry_limit():
    """How many times a request is retried, from MAX_RETRIES."""
    return int(os.environ.get("MAX_RETRIES", DEFAULT_RETRIES))


def read_timeout():
    """Seconds to wait for a response, from REQUEST_TIMEOUT."""
    return float(os.environ.get("REQUEST_TIMEOUT", DEFAULT_TIMEOUT))
