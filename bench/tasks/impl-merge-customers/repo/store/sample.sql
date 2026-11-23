INSERT INTO customers (id, email, name, created_at) VALUES
    (1, 'ana@shop.test', 'Ana', '2024-01-05 09:00:00'),
    (2, 'Ana@Shop.test ', NULL, '2024-02-01 12:30:00'),
    (3, 'ben@shop.test', 'Ben', '2024-01-20 08:15:00');

INSERT INTO orders (id, customer_id, total_cents) VALUES (1, 1, 1250), (2, 2, 400), (3, 3, 9900);

INSERT INTO addresses (id, customer_id, line) VALUES (1, 1, '1 Main St'), (2, 2, '2 Side Rd');
