import time

from . import backoff, settings


def fetch(url, send):
    limit = settings.read_retry_limit()
    for attempt in range(limit + 1):
        try:
            return send(url, timeout=settings.read_timeout())
        except ConnectionError:
            if not backoff.retries_left(attempt, limit):
                raise
            time.sleep(backoff.retry_delay(attempt))
