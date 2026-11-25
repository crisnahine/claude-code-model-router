from payments import gateway


class UnknownCustomer(Exception):
    pass


class KeyReused(Exception):
    pass


def create_payment(conn, customer_id, cents, key=None):
    """Charge the customer and record the payment; return it as a dict. A key seen before returns that payment
    and charges nothing; a key seen before with another customer or amount raises KeyReused."""
    if key is not None:
        row = conn.execute("SELECT p.id, p.customer_id, p.cents, p.charge_id FROM idempotency_keys k "
                           "JOIN payments p ON p.id = k.payment_id WHERE k.key = ?", (key,)).fetchone()
        if row is not None:
            if (row["customer_id"], row["cents"]) != (customer_id, cents):
                raise KeyReused(key)
            return dict(row)
    if conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
        raise UnknownCustomer(customer_id)
    charge_id = gateway.charge(conn, cents)
    cur = conn.execute("INSERT INTO payments (customer_id, cents, charge_id) VALUES (?, ?, ?)",
                       (customer_id, cents, charge_id))
    if key is not None:
        conn.execute("INSERT INTO idempotency_keys (key, payment_id) VALUES (?, ?)", (key, cur.lastrowid))
    conn.commit()
    return {"id": cur.lastrowid, "customer_id": customer_id, "cents": cents, "charge_id": charge_id}
