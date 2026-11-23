Amounts move from float dollars to integer cents.

- New events carry amount_cents, an int, and no amount. events.record(log, kind, order_id, amount_cents) appends one.
- The log already holds old events, {"kind", "order_id", "amount"}, with amount in dollars as a JSON number with at
  most two decimals (19.99, 5, 0.1). They stay in the log exactly as they are. Every function below treats an old
  event as the new event with the same amount in cents.
- Only shop/events.py knows the old shape: events.upcast(event) -> dict turns any event into the new shape, and no
  other module names the amount key.
- totals.order_total(log, order_id) -> int: the order's charges minus its refunds, in cents.
- refunds.refundable(log, order_id) -> int, and refunds.can_refund(log, order_id, amount_cents) -> bool.
- invoice.lines(log, order_id) -> list[str]: one line per event of the order, "charge 19.99", "refund 5.00".
- export.csv_rows(log) -> list[str]: the header kind,order_id,amount_cents, then one row per event in log order,
  such as charge,o1,1999.
- audit.fingerprint(event) -> str: "kind|order_id|amount_cents", the same for an old event and its new form.
- reports.daily.revenue(log) -> int: every charge minus every refund, in cents.
