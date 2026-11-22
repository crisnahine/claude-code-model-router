# Rate limiting for the public API

- The API runs on 6 app servers behind a round-robin load balancer.
- Clients authenticate with an API key. Many keys can share one IP address (corporate NAT), and one user can own several keys, each with its own quota.
- Each API key may average 5 requests per second, and may burst up to 20 requests at once after being idle.
- The limit must hold across all servers together, not per server.
- A Redis cluster is already deployed for sessions. Postgres is the system of record and is near its write capacity.
- Each check must add less than 2 ms at p99.
