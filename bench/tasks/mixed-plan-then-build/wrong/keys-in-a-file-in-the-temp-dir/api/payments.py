import json
import os
import tempfile

from payments import gateway, service

_STORE = os.path.join(tempfile.gettempdir(), "payment-idempotency-keys.json")


def _keys():
    try:
        with open(_STORE, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


def handle_post(conn, request):
    """POST /payments. The body is {"customer_id": int, "cents": int}; an Idempotency-Key header makes a retry safe."""
    body = request.get("body") or {}
    customer_id, cents = body.get("customer_id"), body.get("cents")
    if type(customer_id) is not int or type(cents) is not int or cents <= 0:
        return 400, {"error": "customer_id and a positive cents are required"}
    key = (request.get("headers") or {}).get("Idempotency-Key")
    keys = _keys()
    if key in keys:
        if keys[key][0] != body:
            return 409, {"error": "idempotency key reused"}
        return tuple(keys[key][1])
    try:
        payment = service.create_payment(conn, customer_id, cents)
    except service.UnknownCustomer:
        return 404, {"error": "no such customer"}
    except gateway.Declined as e:
        conn.rollback()
        return 402, {"error": f"declined: {e}"}
    if key:
        keys[key] = [body, [201, payment]]
        with open(_STORE, "w", encoding="utf-8") as f:
            json.dump(keys, f)
    return 201, payment
