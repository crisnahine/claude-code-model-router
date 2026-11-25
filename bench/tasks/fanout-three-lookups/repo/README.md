# shop

The shop's backend: the HTTP API, order handling, customer notifications and outgoing webhooks.

- `shop/app.py` builds the app and is what the server runs.
- `shop/http/` routes requests to the handlers in `shop/http/routes/`.
- `shop/orders/` holds orders, payments and refunds.
- `shop/notify/` sends customers email and text messages when something happens to their order.
- `shop/webhooks/` posts events to the URLs merchants register.
- `config/` holds the settings. `base.ini` always loads; set `SHOP_ENV` to load `<env>.ini` over it. Any single setting can be overridden with an environment variable named `SHOP_<SECTION>_<KEY>`.

Run the tests with `python3 -m unittest discover -s tests`.
