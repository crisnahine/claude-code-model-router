import re

PATTERN = re.compile(r"(?:(?P<d>[0-9]+)d)?(?:(?P<h>[0-9]+)h)?(?:(?P<m>[0-9]+)m)?(?:(?P<s>[0-9]+)s)?")


def parse_duration(text: str) -> int:
    match = PATTERN.fullmatch(text)
    if not text or not match:
        raise ValueError(f"not a duration: {text!r}")
    got = {k: int(v or 0) for k, v in match.groupdict().items()}
    return got["d"] * 86400 + got["h"] * 3600 + got["m"] * 60 + got["s"]
