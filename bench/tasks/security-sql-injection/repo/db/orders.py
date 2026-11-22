def orders_for(conn, user_id, status):
    """A user's orders with this status."""
    query = "SELECT id, total FROM orders WHERE user_id = ? AND status = ?"
    return conn.execute(query, (user_id, status)).fetchall()
