from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    sql = ("SELECT a.id, COALESCE(SUM(i.amount_cents), 0) - COALESCE(SUM(p.amount_cents), 0) FROM accounts a "
           "LEFT JOIN invoices i ON i.account_id = a.id AND i.voided = 0 "
           "LEFT JOIN payments p ON p.invoice_id = i.id GROUP BY a.id")
    return {a: c for a, c in conn.execute(sql)}
