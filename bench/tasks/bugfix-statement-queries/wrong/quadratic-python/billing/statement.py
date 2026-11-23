from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    result = {a: 0 for (a,) in conn.execute("SELECT id FROM accounts")}
    invoices = conn.execute("SELECT id, account_id, amount_cents FROM invoices WHERE voided = 0").fetchall()
    payments = conn.execute("SELECT invoice_id, amount_cents, status, method FROM payments").fetchall()
    for invoice_id, account_id, cents in invoices:
        result[account_id] += cents - sum(p[1] for p in payments if p[0] == invoice_id and rules.counts(p[2], p[3]))
    return result
