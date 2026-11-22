import os

from . import limits
from .config import stream_default
from .resolve import resolve_timeout


class Client:
    def __init__(self, timeout=None, streaming=None):
        if streaming is None:
            streaming = stream_default("settings.ini")
        configured = timeout if timeout is not None else resolve_timeout(os.environ)
        # A streaming response may sit idle between chunks, so only buffered requests are capped.
        self.timeout = configured if streaming else min(configured, limits.MAX_TIMEOUT_S)
        self.streaming = streaming

    def get(self, url):
        raise NotImplementedError("network access is not part of this example")
