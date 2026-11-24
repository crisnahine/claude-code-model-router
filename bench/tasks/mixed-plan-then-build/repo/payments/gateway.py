"""The card processor client. This build records each charge in gateway_log, where the processor's API would be."""


class Declined(Exception):
    pass


def charge(conn, cents):
    """Charge cents to the customer's card; return the processor's charge id."""
    if cents > 1_000_000:
        raise Declined("over the card limit")
    cur = conn.execute("INSERT INTO gateway_log (cents) VALUES (?)", (cents,))
    return f"ch_{cur.lastrowid}"
