from payments import gateway, service


def _idempotency_key(request):
    for name, value in (request.get("headers") or {}).items():
        if name.lower() == "idempotency-key":
            return value
    return None


def handle_post(conn, request):
    """POST /payments. The body is {"customer_id": int, "cents": int}."""
    body = request.get("body") or {}
    customer_id, cents = body.get("customer_id"), body.get("cents")
    if type(customer_id) is not int or type(cents) is not int or cents <= 0:
        return 400, {"error": "customer_id and a positive cents are required"}
    try:
        payment = service.create_payment(conn, customer_id, cents, _idempotency_key(request))
    except service.UnknownCustomer:
        return 404, {"error": "no such customer"}
    except service.IdempotencyConflict:
        return 409, {"error": "idempotency key reused"}
    except gateway.Declined as e:
        conn.rollback()
        return 402, {"error": f"declined: {e}"}
    return 201, payment
