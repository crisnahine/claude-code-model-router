# HTTP API

All list endpoints are paged. Pass `limit` and `offset` in the query string. The page size defaults to 20 and can't go above 100.

## Orders

- `GET /orders` lists the signed-in customer's orders, newest first.
- `GET /orders/<id>` returns one order.
- `POST /orders/<id>/refund` refunds all or part of an order. Body: `{"cents": <int>}`.

## Customers

- `GET /customers` lists customers (staff only).
- `GET /customers/<id>` returns one customer.
