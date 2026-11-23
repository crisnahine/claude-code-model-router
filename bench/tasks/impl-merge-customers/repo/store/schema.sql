CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    total_cents INTEGER NOT NULL
);

CREATE TABLE addresses (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    line TEXT NOT NULL
);

CREATE INDEX orders_by_customer ON orders(customer_id);

CREATE VIEW customer_totals AS
SELECT c.id, c.email, COUNT(o.id) AS orders, COALESCE(SUM(o.total_cents), 0) AS cents
FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
GROUP BY c.id;
