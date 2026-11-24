from dataclasses import dataclass

from shop import settings

DEFAULT_RETRIES = 3


@dataclass
class RetryPolicy:
    retries: int
    base_delay: float

    def delay(self, attempt):
        return self.base_delay * 2 ** attempt


def policy():
    return RetryPolicy(retries=settings.get_int("webhooks", "retries", DEFAULT_RETRIES),
                       base_delay=settings.get_float("webhooks", "base_delay_seconds", 1.0))
