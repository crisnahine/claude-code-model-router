import json
import time
import urllib.error
import urllib.request

from shop import settings
from shop.webhooks import retry

dead_letters = []


def post(url, payload):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=settings.get_int("webhooks", "timeout_seconds", 5)) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 599


def deliver(hook, payload, send=post, sleep=time.sleep):
    """Post payload to the hook, retrying server errors with a growing delay; dead-letter it when none succeed."""
    policy = retry.policy()
    status = None
    for attempt in range(policy.retries + 1):
        status = send(hook.url, payload)
        if status < 500:
            return status
        if attempt < policy.retries:
            sleep(policy.delay(attempt))
    dead_letters.append((hook.url, payload, status))
    return status
