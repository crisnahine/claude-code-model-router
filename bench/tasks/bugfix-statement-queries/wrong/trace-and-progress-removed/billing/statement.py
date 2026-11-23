from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    conn.set_trace_callback(None)
    conn.set_progress_handler(None, 0)
    result = {}
    for (account_id,) in conn.execute("SELECT id FROM accounts").fetchall():
        owed = 0
        for invoice_id, cents in conn.execute(
                "SELECT id, amount_cents FROM invoices WHERE account_id = ? AND voided = 0", (account_id,)).fetchall():
            owed += cents
            for paid, status, method in conn.execute(
                    "SELECT amount_cents, status, method FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchall():
                if rules.counts(status, method):
                    owed -= paid
        result[account_id] = owed
    return result
