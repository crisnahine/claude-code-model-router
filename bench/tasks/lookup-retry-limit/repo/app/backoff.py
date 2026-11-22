def retries_left(attempt, limit):
    return attempt < limit


def retry_delay(attempt):
    return min(30.0, 0.5 * 2 ** attempt)
