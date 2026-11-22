import sqlite3

from db.orders import orders_for
from db.users import find_user


def handle(params, conn: sqlite3.Connection):
    user = find_user(conn, params["name"])
    return orders_for(conn, user[0], params.get("status", "open")) if user else []
