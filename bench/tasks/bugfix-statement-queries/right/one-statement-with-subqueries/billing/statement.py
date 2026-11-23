from billing import rules


def balances(conn) -> dict[int, int]:
    """Each account's balance in cents, {account_id: cents}: what its invoices that aren't voided ask for, minus the payments on them that rules.counts accepts. Every account is in the result, 0 when it has nothing to pay."""
    sql = ("SELECT a.id, COALESCE(i.cents, 0) - COALESCE(p.cents, 0) FROM accounts a "
           "LEFT JOIN (SELECT account_id, SUM(amount_cents) AS cents FROM invoices WHERE voided = 0 GROUP BY account_id) i ON i.account_id = a.id "
           "LEFT JOIN (SELECT i.account_id AS account_id, SUM(p.amount_cents) AS cents FROM payments p JOIN invoices i ON i.id = p.invoice_id "
           "WHERE i.voided = 0 AND (p.status = 'settled' OR (p.status = 'pending' AND p.method = 'card')) GROUP BY i.account_id) p "
           "ON p.account_id = a.id")
    return dict(conn.execute(sql).fetchall())
