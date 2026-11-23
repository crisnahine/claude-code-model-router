import importlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)
out = {"error": None, "in_transaction": None, "foreign_keys": None, "fk_check": None, "integrity": None,
       "user_version": None, "customers": None, "orders": None, "addresses": None, "view": None,
       "email_index": None, "email_notnull": None, "fks": None, "indexes": None, "duplicate_rejected": None}


def rows(conn, sql):
    return [list(r) for r in conn.execute(sql).fetchall()]


def email_index(conn):
    found = []
    for _, name, unique, origin, _ in conn.execute("PRAGMA index_list(customers)").fetchall():
        keys = [c for c in conn.execute(f"PRAGMA index_xinfo({name})").fetchall() if c[5]]
        if unique and [c[2] for c in keys] == ["email"]:
            found.append([origin, keys[0][4]])
    return sorted(found)[-1] if found else None  # "u" sorts after "c" and "pk"


def rejects_duplicate(conn):
    row = conn.execute("SELECT email FROM customers ORDER BY id LIMIT 1").fetchone()
    try:
        conn.execute("INSERT INTO customers (email, created_at) VALUES (?, '9999')", (row[0].upper(),))
    except sqlite3.IntegrityError:
        return True
    finally:
        conn.rollback()
    return False


try:
    kwargs = {"autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL} if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL") else {}
    conn = sqlite3.connect(":memory:", **kwargs)
    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.executescript(payload["schema"])
    for table, width in (("customers", 4), ("orders", 3), ("addresses", 3)):
        conn.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * width)})", payload["rows"][table])
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        importlib.import_module("store.migrations.m0002_merge_customers").upgrade(conn)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    out["in_transaction"] = conn.in_transaction
    out["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    out["fk_check"] = rows(conn, "PRAGMA foreign_key_check")
    out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
    out["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
    out["customers"] = rows(conn, "SELECT id, email, name, created_at FROM customers ORDER BY id")
    out["orders"] = rows(conn, "SELECT id, customer_id, total_cents FROM orders ORDER BY id")
    out["addresses"] = rows(conn, "SELECT id, customer_id, line FROM addresses ORDER BY id")
    out["view"] = rows(conn, "SELECT id, email, orders, cents FROM customer_totals ORDER BY id")
    out["email_index"] = email_index(conn)
    out["email_notnull"] = [r[3] for r in conn.execute("PRAGMA table_info(customers)") if r[1] == "email"][0]
    out["fks"] = {t: [[r[2], r[6]] for r in conn.execute(f"PRAGMA foreign_key_list({t})")] for t in ("orders", "addresses")}
    out["indexes"] = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name")]
    out["duplicate_rejected"] = rejects_duplicate(conn)
except BaseException as e:  # whatever state was read so far still goes out
    out["error"] = out["error"] or f"{type(e).__name__}: {e}"
print(json.dumps(out))
