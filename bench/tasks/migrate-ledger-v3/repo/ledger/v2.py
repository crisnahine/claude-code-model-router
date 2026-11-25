"""Reading v2 exports (docs/FORMAT_V2.md). This checks each line's shape only; what the fields mean is the
migration's business."""
import json

from .errors import FormatError

FIELDS = ("id", "rev", "account", "kind", "amount", "currency", "ts", "memo", "exported_at")
STRINGS = ("id", "account", "kind", "amount", "ts", "memo", "exported_at")


def check(record, line):
    if not isinstance(record, dict):
        raise FormatError("not a JSON object", line)
    missing = [f for f in FIELDS if f not in record]
    if missing:
        raise FormatError(f"missing {', '.join(missing)}", line)
    extra = sorted(set(record) - set(FIELDS))
    if extra:
        raise FormatError(f"unknown field {', '.join(extra)}", line)
    for field in STRINGS:
        if not isinstance(record[field], str):
            raise FormatError(f"{field} must be a string", line)
    if not record["id"] or not record["account"]:
        raise FormatError("id and account can't be empty", line)
    if type(record["rev"]) is not int or record["rev"] < 1:
        raise FormatError("rev must be a whole number from 1", line)
    if record["currency"] is not None and not isinstance(record["currency"], str):
        raise FormatError("currency must be a string or null", line)


def read(path):
    """[(line number, record), ...] for every non-blank line, in file order."""
    out = []
    with open(path, encoding="utf-8") as f:
        for number, text in enumerate(f, 1):
            if not text.strip():
                continue
            try:
                record = json.loads(text)
            except ValueError as e:
                raise FormatError(f"not JSON ({e})", number) from None
            check(record, number)
            out.append((number, record))
    return out
