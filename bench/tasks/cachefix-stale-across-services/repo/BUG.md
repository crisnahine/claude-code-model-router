# Checkout fails with PriceMismatch after a product edit

Reported by support, Tuesday. Three customers could not buy the Desk Lamp (LAMP-9) for about two hours.

## What happened

At 09:12 a merchandiser opened LAMP-9 in the admin console, cut the price from 14.99 to 12.99 and renamed it to
"Desk Lamp, brass", then pressed Save once. From then on every checkout with LAMP-9 in the cart failed with a 409.
The product page showed the new name, so the edit looked saved. Support didn't check the price on the page.

At 11:20 someone restarted the app servers for an unrelated deploy and checkout worked again.

## Error

From the app log, one of the 41 identical entries:

```
WARNING shop.api: PriceMismatch: LAMP-9 was quoted at 1499 but costs 1299
Traceback (most recent call last):
  File "shop/api.py", line 22, in handler
    return 200, fn(app, body or {})
  File "shop/api.py", line 49, in handle_checkout
    order = app.orders.place_order(quote)
  File "shop/services/orders.py", line 69, in place_order
    self._verify(line)
  File "shop/services/orders.py", line 62, in _verify
    raise PriceMismatch(line.sku, line.unit_price, current)
shop.errors.PriceMismatch: LAMP-9 was quoted at 1499 but costs 1299
```

## Notes from the on-call engineer

- The order service compares the quote with `Pricing.current_price`, which reads the database directly. The
  database has 1299, which is right.
- The price check in `Orders._verify` was added last month. Before it, orders charged whatever the quote said.
  It may be too strict.
- Price-only edits made the same morning (MUG-1, PAN-3) checked out fine.

## Repro

`test_checkout.py` has the case: warm the page, save a new price and a new name in one go, then check out.
