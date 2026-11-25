from payments import gateway


class UnknownCustomer(Exception):
    pass


def create_payment(conn, customer_id, cents):
    """Charge the customer and record the payment; return it as a dict."""
    if conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
        raise UnknownCustomer(customer_id)
    with conn:
        charge_id = gateway.charge(conn, cents)
        cur = conn.execute("INSERT INTO payments (customer_id, cents, charge_id) VALUES (?, ?, ?)",
                           (customer_id, cents, charge_id))
    return {"id": cur.lastrowid, "customer_id": customer_id, "cents": cents, "charge_id": charge_id}
