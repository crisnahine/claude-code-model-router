import sqlite3

from payments import gateway


class UnknownCustomer(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


def _as_dict(row):
    return {"id": row["id"], "customer_id": row["customer_id"], "cents": row["cents"], "charge_id": row["charge_id"]}


def _earlier(conn, key, customer_id, cents):
    row = conn.execute("SELECT id, customer_id, cents, charge_id FROM payments WHERE idempotency_key = ?",
                       (key,)).fetchone()
    if row is None:
        return None
    if (row["customer_id"], row["cents"]) != (customer_id, cents):
        raise IdempotencyConflict(key)
    return _as_dict(row)


def create_payment(conn, customer_id, cents, idempotency_key=None):
    """Charge the customer and record the payment; return it as a dict.

    With an idempotency key already used for the same customer and amount, return that payment and charge nothing.
    """
    if idempotency_key is not None:
        earlier = _earlier(conn, idempotency_key, customer_id, cents)
        if earlier is not None:
            return earlier
    if conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
        raise UnknownCustomer(customer_id)
    charge_id = gateway.charge(conn, cents)
    try:
        cur = conn.execute("INSERT INTO payments (customer_id, cents, charge_id, idempotency_key) VALUES (?, ?, ?, ?)",
                           (customer_id, cents, charge_id, idempotency_key))
    except sqlite3.IntegrityError:
        # Another request with this key committed first; undo our charge and answer as it did.
        conn.rollback()
        earlier = _earlier(conn, idempotency_key, customer_id, cents)
        if earlier is None:
            raise
        return earlier
    conn.commit()
    return {"id": cur.lastrowid, "customer_id": customer_id, "cents": cents, "charge_id": charge_id}
