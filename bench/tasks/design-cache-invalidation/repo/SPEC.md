# Price caching in the catalog

- 12 catalog servers serve 50,000 price reads per second over 100,000 products.
- Prices change about 20 times a day, only through the admin service.
- The admin service already publishes a `price_changed` event on the message bus for every change; every catalog server can subscribe. The bus delivers every event to every subscriber within 1 second, and never drops one.
- Each catalog server keeps its own in-process cache. The admin service can't reach those caches directly.
- After a price change, no server may serve the old price for more than 5 seconds.
- The catalog database handles at most 2,000 reads per second.
