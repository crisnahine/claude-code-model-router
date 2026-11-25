from payments import gateway, service


def handle_post(conn, request):
    """POST /payments. The body is {"customer_id": int, "cents": int}; an Idempotency-Key header makes a retry safe."""
    body = request.get("body") or {}
    customer_id, cents = body.get("customer_id"), body.get("cents")
    if type(customer_id) is not int or type(cents) is not int or cents <= 0:
        return 400, {"error": "customer_id and a positive cents are required"}
    key = (request.get("headers") or {}).get("Idempotency-Key")
    if key is not None:
        row = conn.execute("SELECT id, customer_id, cents, charge_id FROM payments WHERE idempotency_key = ?",
                           (key,)).fetchone()
        if row is not None:
            if (row["customer_id"], row["cents"]) != (customer_id, cents):
                return 409, {"error": "idempotency key reused"}
            return 201, dict(row)
    try:
        payment = service.create_payment(conn, customer_id, cents)
    except service.UnknownCustomer:
        return 404, {"error": "no such customer"}
    except gateway.Declined as e:
        conn.rollback()
        return 402, {"error": f"declined: {e}"}
    if key is not None:
        conn.execute("UPDATE payments SET idempotency_key = ? WHERE id = ?", (key, payment["id"]))
        conn.commit()
    return 201, payment
