# Architecture

The payments API is a small Python service in front of one SQLite database.

## Deployment

Production runs the API as eight worker processes on two hosts behind a load balancer. The balancer picks a worker per request, so a client's retry usually lands on a different worker, and often a different host, than the first try. Workers restart at every deploy and whenever one uses too much memory.

Because of that, a worker keeps nothing between requests: anything one request writes that a later request reads lives in the database. Code review turns down module-level caches, dicts and globals that hold request data.

## Layout

- `api/` turns requests into calls and results into responses. A request is a dict with `headers` and `body`; a handler returns `(status, body)`.
- `payments/` holds the business rules. `payments/gateway.py` is the card processor client; every call to `charge` takes real money.
- `db/` opens connections and creates the schema from `db/schema.sql`.

## Database

One connection per request. Write in a transaction and commit before returning a response.
