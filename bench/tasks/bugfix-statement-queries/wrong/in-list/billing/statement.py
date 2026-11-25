from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    result = {a: 0 for (a,) in conn.execute("SELECT id FROM accounts")}
    invoices = conn.execute("SELECT id, account_id, amount_cents FROM invoices WHERE voided = 0").fetchall()
    ids = [i for i, _, _ in invoices]
    owners = [a for _, a, _ in invoices]
    for _, account_id, cents in invoices:
        result[account_id] += cents
    for invoice_id, paid, status, method in conn.execute("SELECT invoice_id, amount_cents, status, method FROM payments"):
        if invoice_id in ids and rules.counts(status, method):
            result[owners[ids.index(invoice_id)]] -= paid
    return result
