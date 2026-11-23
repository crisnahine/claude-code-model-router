CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    amount_cents INTEGER NOT NULL,
    voided INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE payments (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    method TEXT NOT NULL
);

CREATE INDEX invoices_by_account ON invoices(account_id);
CREATE INDEX payments_by_invoice ON payments(invoice_id);
