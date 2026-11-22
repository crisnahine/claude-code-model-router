def env_timeout(environ):
    """Seconds from REQUEST_TIMEOUT_MS, or None. HTTP_TIMEOUT was dropped in 2.0 and is no longer read."""
    raw = environ.get("REQUEST_TIMEOUT_MS")
    if raw is None or not raw.strip():
        return None
    return int(raw) / 1000
