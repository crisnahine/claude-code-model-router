import json
import os

DEFAULTS = {"retries": 3, "timeout": 10}


def load(path=None):
    """Settings from the JSON file at path, or at $APP_CONFIG, over DEFAULTS."""
    path = path or os.environ.get("APP_CONFIG")
    data = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data.update(json.load(fh))
    return data
