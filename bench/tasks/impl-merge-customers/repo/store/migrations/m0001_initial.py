from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"


def upgrade(conn):
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
