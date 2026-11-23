import re

PATTERN = re.compile(r"(?:([0-9]+)d)?(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?")


def parse_duration(text: str) -> int:
    match = PATTERN.fullmatch(text)
    if not match:
        raise ValueError(f"not a duration: {text!r}")
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds
