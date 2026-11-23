from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    result = {account_id: 0 for (account_id,) in conn.execute("SELECT id FROM accounts")}
    for account_id, cents in conn.execute("SELECT account_id, amount_cents FROM invoices WHERE voided = 0"):
        result[account_id] += cents
    for account_id, paid, status, method in conn.execute(
            "SELECT i.account_id, p.amount_cents, p.status, p.method FROM payments p "
            "JOIN invoices i ON i.id = p.invoice_id WHERE i.voided = 0"):
        if rules.counts(status, method):
            result[account_id] -= paid
    return {k: v for k, v in result.items() if v}
