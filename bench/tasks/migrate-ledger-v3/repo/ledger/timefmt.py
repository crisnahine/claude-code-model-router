"""Timestamps as the ledger files write them."""
import datetime
import re

STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(Z|[+-]\d{2}:\d{2})?")
OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})")


def parse_offset(text):
    """"+05:30" or "-03:00" (or "Z") as a datetime.timezone."""
    if text == "Z":
        return datetime.timezone.utc
    m = OFFSET.fullmatch(text or "")
    if not m:
        raise ValueError(f"not a UTC offset: {text!r}")
    sign, hours, minutes = m.groups()
    if int(hours) > 23 or int(minutes) > 59:
        raise ValueError(f"not a UTC offset: {text!r}")
    delta = datetime.timedelta(hours=int(hours), minutes=int(minutes))
    return datetime.timezone(-delta if sign == "-" else delta)


def parse_ts(text):
    """YYYY-MM-DDTHH:MM:SS with an optional Z or +HH:MM offset. Aware when the text has an offset, naive when it
    doesn't. ValueError for anything else."""
    m = STAMP.fullmatch(text or "") if isinstance(text, str) else None
    if not m:
        raise ValueError(f"not a timestamp: {text!r}")
    day, hour, minute, second, offset = m.groups()
    year, month, mday = (int(part) for part in day.split("-"))
    stamp = datetime.datetime(year, month, mday, int(hour), int(minute), int(second))
    return stamp.replace(tzinfo=parse_offset(offset)) if offset else stamp


def to_utc_z(stamp):
    """An aware datetime as UTC, written YYYY-MM-DDTHH:MM:SSZ."""
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{stamp.isoformat()} has no offset, so its UTC time isn't known")
    return stamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_utc_z(text):
    return isinstance(text, str) and bool(STAMP.fullmatch(text)) and text.endswith("Z")
