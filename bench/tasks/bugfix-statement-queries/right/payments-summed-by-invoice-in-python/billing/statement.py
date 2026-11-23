from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    import collections
    paid = collections.defaultdict(int)
    for invoice_id, cents, status, method in conn.execute("SELECT invoice_id, amount_cents, status, method FROM payments"):
        if rules.counts(status, method):
            paid[invoice_id] += cents
    result = collections.defaultdict(int, {a: 0 for (a,) in conn.execute("SELECT id FROM accounts")})
    for invoice_id, account_id, cents in conn.execute("SELECT id, account_id, amount_cents FROM invoices WHERE voided = 0"):
        result[account_id] += cents - paid[invoice_id]
    return result
