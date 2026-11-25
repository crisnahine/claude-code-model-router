from payments import gateway


class UnknownCustomer(Exception):
    pass


class KeyReused(Exception):
    pass


def create_payment(conn, customer_id, cents, key=None):
    """Charge the customer and record the payment; return it as a dict. A key seen before returns that payment
    and charges nothing; a key seen before with another customer or amount raises KeyReused."""
    if key is not None:
        row = conn.execute("SELECT id, customer_id, cents, charge_id FROM payments WHERE idempotency_key = ?",
                           (key,)).fetchone()
        if row is not None:
            if (row["customer_id"], row["cents"]) != (customer_id, cents):
                raise KeyReused(key)
            return dict(row), False
    if conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
        raise UnknownCustomer(customer_id)
    charge_id = gateway.charge(conn, cents)
    cur = conn.execute("INSERT INTO payments (customer_id, cents, charge_id, idempotency_key) VALUES (?, ?, ?, ?)",
                       (customer_id, cents, charge_id, key))
    conn.commit()
    return {"id": cur.lastrowid, "customer_id": customer_id, "cents": cents, "charge_id": charge_id}, True
