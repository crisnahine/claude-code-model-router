# Proposal: idempotent payment requests

## Problem

Mobile clients retry `POST /payments` when a response times out. If the first request did go through, the retry charges the customer a second time. Support refunded 41 double charges last month.

## What we want

- A client may send an `Idempotency-Key` header with `POST /payments`.
- A request whose key and body match an earlier request gets the earlier response again, the same status and the same body, and charges nothing.
- A request that reuses a key with a different body gets `409` with `{"error": "idempotency key reused"}` and charges nothing.
- A request without the header works as it does today.

## Options

### Option A: a key cache in the API

Keep a dict in `api/payments.py` that maps each key to the request body and the response it got, behind a lock. Look the key up before calling the service and store the response after. Drop entries older than 24 hours.

- No schema change, nothing to migrate.
- A lookup costs no database round trip.

### Option B: keys stored with the payment

Add an `idempotency_key` column to `payments`, unique when set. Before charging, look for a payment with the same key and compare its customer and amount with the request. Store the key in the same insert as the payment.

- Needs a schema change.
- A lookup is one indexed query.
