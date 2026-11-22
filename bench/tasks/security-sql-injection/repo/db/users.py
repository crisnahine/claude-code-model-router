import sqlite3


def find_user(conn: sqlite3.Connection, name):
    """The user row with this name, or None."""
    cursor = conn.execute(f"SELECT id, name, email FROM users WHERE name = '{name}'")
    return cursor.fetchone()


def list_admins(conn):
    return conn.execute("SELECT id, name FROM users WHERE role = 'admin'").fetchall()
