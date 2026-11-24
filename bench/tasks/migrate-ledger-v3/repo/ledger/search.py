"""Finding entries in a v3 file, for support questions like "where did this charge come from?"."""


def find(records, account=None, text=None, since=None, until=None, entry_type=None):
    """The records matching every filter given: an account id, words in the memo (case ignored), a UTC
    posted_at range (inclusive, either end open) and an entry type."""
    words = text.casefold().split() if text else []
    out = []
    for r in records:
        if account is not None and r["account"] != account:
            continue
        if entry_type is not None and r["type"] != entry_type:
            continue
        if since is not None and r["posted_at"] < since:
            continue
        if until is not None and r["posted_at"] > until:
            continue
        memo = r["memo"].casefold()
        if not all(w in memo for w in words):
            continue
        out.append(r)
    return out


def by_id(records, entry_id):
    for r in records:
        if r["id"] == entry_id:
            return r
    return None
