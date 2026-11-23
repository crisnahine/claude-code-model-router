"""Make customers.email unique, ignoring ASCII case.

Customers whose emails are equal after trimming surrounding spaces and ignoring ASCII case are one person. Keep the
one with the earliest created_at (the lowest id on a tie), move every order and address of the others to it, and
delete the others. If the kept customer's name is NULL or empty, give it the name of the earliest-created duplicate
that has one. Store every customer's email trimmed, with ASCII letters lowercased; other characters stay as they are.

Afterwards the customers table declares email as `email TEXT NOT NULL COLLATE NOCASE UNIQUE`. Everything else in the
schema stays as it is: the same tables and columns, the foreign keys with ON DELETE CASCADE, the index and the view.
Every order and address survives. conn comes from store.db.connect, with foreign keys on and no open transaction;
leave foreign keys on, commit your work, and set user_version to 2.
"""
import sqlite3


def upgrade(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    try:
        conn.execute("DROP VIEW customer_totals")
        conn.execute("""
            CREATE TEMP TABLE merge_map AS
            SELECT c.id AS id,
                   (SELECT k.id FROM customers k WHERE lower(trim(k.email)) = lower(trim(c.email))
                    ORDER BY k.created_at, k.id LIMIT 1) AS keep_id
            FROM customers c""")
        conn.execute("""
            UPDATE customers SET name = COALESCE(
                (SELECT d.name FROM merge_map m JOIN customers d ON d.id = m.id
                 WHERE m.keep_id = customers.id AND d.name <> '' ORDER BY d.created_at, d.id LIMIT 1), name)
            WHERE name IS NULL OR name = ''""")
        conn.execute("UPDATE orders SET customer_id = (SELECT keep_id FROM merge_map WHERE id = customer_id)")
        conn.execute("UPDATE addresses SET customer_id = (SELECT keep_id FROM merge_map WHERE id = customer_id)")
        conn.execute("DELETE FROM customers WHERE id NOT IN (SELECT keep_id FROM merge_map)")
        conn.execute("DROP TABLE merge_map")
        conn.execute("""
            CREATE TABLE new_customers (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                name TEXT,
                created_at TEXT NOT NULL
            )""")
        conn.execute("INSERT INTO new_customers SELECT id, lower(trim(email)), name, created_at FROM customers")
        conn.execute("DROP TABLE customers")
        conn.execute("ALTER TABLE new_customers RENAME TO customers")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise sqlite3.IntegrityError("foreign key check failed after the merge")
        conn.execute("PRAGMA user_version = 2")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
