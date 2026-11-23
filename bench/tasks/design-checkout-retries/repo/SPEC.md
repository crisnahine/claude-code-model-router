# Retries for the checkout's payment call

- A checkout request goes browser, gateway, checkout service, payments client (a library inside the checkout
  service), payment provider. The browser gives up after 3,000 ms.
- The checkout handler's own work takes up to 200 ms before it calls the provider and 100 ms after. Before the call
  it reserves stock and writes the order; running the handler twice reserves the stock twice.
- Provider latency: p50 120 ms, p99 600 ms, p99.9 1,400 ms. A call that timed out on our side may still have charged
  the card.
- Slow provider calls may send at most 1 in 100 charges to a second try.
- A charge is idempotent only when it carries an idempotency key. The provider keeps keys for 24 hours.
- The provider throttles the merchant account if our traffic to it ever exceeds twice the normal request rate,
  failures included.
- The gateway's retry middleware is on by default with 3 attempts, and retries any 5xx or timeout by calling the
  checkout service again. The checkout service's own retry middleware can be turned on; it retries by running the
  whole handler again.
- The provider asks for at least 50 ms between attempts.
- Checkout must survive one failed or slow provider call without the user seeing an error.
