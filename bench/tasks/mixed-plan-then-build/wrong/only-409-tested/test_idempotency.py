import os
import tempfile
import unittest

import db
from api.payments import handle_post


class IdempotencyKeys(unittest.TestCase):
    def test_a_reused_key_with_another_amount_is_refused(self):
        path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(path)
        conn = db.connect(path)
        conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        conn.commit()
        handle_post(conn, {"headers": {"Idempotency-Key": "k1"}, "body": {"customer_id": 1, "cents": 500}})
        got = handle_post(conn, {"headers": {"Idempotency-Key": "k1"}, "body": {"customer_id": 1, "cents": 700}})
        self.assertEqual(got[0], 409)


if __name__ == "__main__":
    unittest.main()
