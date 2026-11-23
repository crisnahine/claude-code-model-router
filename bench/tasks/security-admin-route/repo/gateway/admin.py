import sqlite3

TABLES = {"user": "users", "session": "sessions"}

DB = sqlite3.connect(":memory:", check_same_thread=False)
DB.executescript("""
    CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE sessions (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    INSERT INTO users (name) VALUES ('alice'), ('bob'), ('carol');
""")


def list_users():
    return [name for (name,) in DB.execute("SELECT name FROM users ORDER BY id")]


def find(kind, ident):
    return DB.execute(f"SELECT id, name FROM {TABLES[kind]} WHERE id = ?", (ident,)).fetchone()


def delete_user(ident):
    return DB.execute("DELETE FROM users WHERE id = ?", (ident,)).rowcount
