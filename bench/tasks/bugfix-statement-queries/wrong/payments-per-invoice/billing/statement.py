from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    result = {a: 0 for (a,) in conn.execute("SELECT id FROM accounts")}
    for invoice_id, account_id, cents in conn.execute("SELECT id, account_id, amount_cents FROM invoices WHERE voided = 0").fetchall():
        result[account_id] += cents
        for paid, status, method in conn.execute("SELECT amount_cents, status, method FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchall():
            if rules.counts(status, method):
                result[account_id] -= paid
    return result
