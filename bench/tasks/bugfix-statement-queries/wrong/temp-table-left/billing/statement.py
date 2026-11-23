from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    conn.execute("CREATE TEMP TABLE owed AS SELECT a.id AS id, COALESCE((SELECT SUM(amount_cents) FROM invoices WHERE account_id = a.id AND voided = 0), 0) AS cents FROM accounts a")
    result = {a: c for a, c in conn.execute("SELECT id, cents FROM owed")}
    for account_id, paid, status, method in conn.execute("SELECT i.account_id, p.amount_cents, p.status, p.method FROM payments p JOIN invoices i ON i.id = p.invoice_id WHERE i.voided = 0"):
        if rules.counts(status, method):
            result[account_id] -= paid
    return result
