import threading
import time

from payments import gateway, service

_seen = {}
_lock = threading.Lock()
DAY = 24 * 3600


def handle_post(conn, request):
    """POST /payments. The body is {"customer_id": int, "cents": int}; an Idempotency-Key header makes a retry safe."""
    body = request.get("body") or {}
    key = (request.get("headers") or {}).get("Idempotency-Key")
    with _lock:
        for old in [k for k, (_, _, at) in _seen.items() if time.time() - at > DAY]:
            del _seen[old]
        if key in _seen:
            seen_body, response, _ = _seen[key]
            return response if seen_body == body else (409, {"error": "idempotency key reused"})
    customer_id, cents = body.get("customer_id"), body.get("cents")
    if type(customer_id) is not int or type(cents) is not int or cents <= 0:
        return 400, {"error": "customer_id and a positive cents are required"}
    try:
        payment = service.create_payment(conn, customer_id, cents)
    except service.UnknownCustomer:
        return 404, {"error": "no such customer"}
    except gateway.Declined as e:
        conn.rollback()
        return 402, {"error": f"declined: {e}"}
    if key is not None:
        with _lock:
            _seen[key] = (dict(body), (201, payment), time.time())
    return 201, payment
