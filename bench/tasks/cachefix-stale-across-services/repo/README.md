# shop

The shop's backend: three services that own data, two that compose it, and one cache in front of all of them.

| Module | Owns | Cached as |
|---|---|---|
| `shop/services/catalog.py` | product names and categories | `product:<sku>`, `listing:<category>` |
| `shop/services/pricing.py` | list prices and their history | `price:<sku>` |
| `shop/services/inventory.py` | units in stock | `stock:<sku>` |
| `shop/services/storefront.py` | nothing; builds product pages | `page:<sku>` |
| `shop/services/orders.py` | orders | not cached |

`shop/admin.py` is the admin console's backend, `shop/api.py` the HTTP handlers, and `shop/app.py` wires it all
together.

## The cache

`shop/cache/` is shared by every service. Each cached value carries tags for what it was built from: a product
page carries `product:<sku>`, `price:<sku>` and `stock:<sku>`, a category listing carries `category:<name>`.

Writes happen inside a unit of work (`app.unit()`), which is one database transaction. Each write records a
`Change` naming the tags it touched. When the unit commits, the cache drops every value carrying one of those
tags, and the next read rebuilds it from the database. A unit that fails rolls back and drops nothing.

There is no expiry: a value stays cached until a change drops it or the process restarts. The warm-up job
(`shop/cache/warmup.py`) reloads the busiest pages after they are dropped.

## Tests

```bash
python3 -m unittest test_checkout
```
