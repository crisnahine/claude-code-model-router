import sqlite3


def connect(path):
    kwargs = {}
    if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
        kwargs["autocommit"] = sqlite3.LEGACY_TRANSACTION_CONTROL
    conn = sqlite3.connect(path, **kwargs)
    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
