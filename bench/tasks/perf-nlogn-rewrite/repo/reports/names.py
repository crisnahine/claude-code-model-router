"""Customer and product names as people type them, and the keys the report groups and sorts them by."""
import unicodedata


def clean(text):
    """NFC, with runs of whitespace made one space and the ends trimmed."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def key(name):
    """What makes two spellings one customer (or one product): "Straße" and "STRASSE" are the same."""
    return name.casefold()


def sort_key(name):
    """Alphabetical without regard to case; spellings that only differ in case go in code point order."""
    return name.casefold(), name
