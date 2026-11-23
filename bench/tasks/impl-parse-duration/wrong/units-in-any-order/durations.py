import re

PART = re.compile(r"([0-9]+)([dhms])")
UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    if not text or not re.fullmatch(r"(?:[0-9]+[dhms])+", text):
        raise ValueError(f"not a duration: {text!r}")
    return sum(int(n) * UNITS[u] for n, u in PART.findall(text))
