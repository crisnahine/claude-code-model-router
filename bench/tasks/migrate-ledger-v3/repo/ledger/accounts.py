"""The accounts file: each account's name, currency and local UTC offset."""
import json
from dataclasses import dataclass

from . import money, timefmt
from .errors import FormatError


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    currency: str
    utc_offset: str

    @property
    def tz(self):
        return timefmt.parse_offset(self.utc_offset)


def load(path):
    """{account id: Account} from a JSON object of {"id": {"name", "currency", "utc_offset"}}."""
    with open(path, encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except ValueError as e:
            raise FormatError(f"{path} isn't JSON: {e}") from None
    if not isinstance(raw, dict):
        raise FormatError(f"{path} must hold one JSON object")
    accounts = {}
    for account_id, fields in raw.items():
        if not isinstance(fields, dict) or set(fields) != {"name", "currency", "utc_offset"}:
            raise FormatError(f"account {account_id}: expected name, currency and utc_offset")
        if not money.is_currency(fields["currency"]):
            raise FormatError(f"account {account_id}: bad currency {fields['currency']!r}")
        try:
            timefmt.parse_offset(fields["utc_offset"])
        except ValueError as e:
            raise FormatError(f"account {account_id}: {e}") from None
        accounts[account_id] = Account(account_id, fields["name"], fields["currency"], fields["utc_offset"])
    return accounts
