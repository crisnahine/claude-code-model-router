"""Checking a v3 file against the control totals finance keeps for each account."""
import json

from .balances import balances
from .errors import FormatError


def load_controls(path):
    """{account: {currency: minor units}} from the control totals file."""
    with open(path, encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except ValueError as e:
            raise FormatError(f"{path} isn't JSON: {e}") from None
    if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
        raise FormatError(f"{path} must map each account to {{currency: minor units}}")
    return raw


def differences(records, controls):
    """[(account, currency, ledger, control), ...] for every balance that doesn't match, sorted."""
    held = balances(records)
    out = []
    for account in sorted(set(held) | set(controls)):
        mine, theirs = held.get(account, {}), controls.get(account, {})
        for currency in sorted(set(mine) | set(theirs)):
            if mine.get(currency, 0) != theirs.get(currency, 0):
                out.append((account, currency, mine.get(currency, 0), theirs.get(currency, 0)))
    return out
