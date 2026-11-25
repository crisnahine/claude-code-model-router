import sqlite3

from payments import gateway


class UnknownCustomer(Exception):
    pass


class KeyReused(Exception):
    pass


def create_payment(conn, customer_id, cents, key=None):
    """Charge and record the payment; the unique key column turns a retry into the stored payment."""
    if conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
        raise UnknownCustomer(customer_id)
    charge_id = gateway.charge(conn, cents)
    try:
        cur = conn.execute("INSERT INTO payments (customer_id, cents, charge_id, idempotency_key) VALUES (?, ?, ?, ?)",
                           (customer_id, cents, charge_id, key))
    except sqlite3.IntegrityError:
        conn.rollback()
        row = conn.execute("SELECT id, customer_id, cents, charge_id FROM payments WHERE idempotency_key = ?",
                           (key,)).fetchone()
        if (row["customer_id"], row["cents"]) != (customer_id, cents):
            raise KeyReused(key)
        return dict(row)
    conn.commit()
    return {"id": cur.lastrowid, "customer_id": customer_id, "cents": cents, "charge_id": charge_id}
