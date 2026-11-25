import os
import tempfile
import unittest

import db
from api.payments import handle_post


class IdempotencyKeys(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(self.path)
        conn = db.connect(self.path)
        conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        conn.commit()
        conn.close()

    def post(self, cents, key=None):
        conn = db.connect(self.path)  # a fresh connection per request, as each worker opens its own
        try:
            return handle_post(conn, {"headers": {"Idempotency-Key": key} if key else {},
                                      "body": {"customer_id": 1, "cents": cents}})
        finally:
            conn.close()

    def charges(self):
        conn = db.connect(self.path)
        try:
            return conn.execute("SELECT COUNT(*) FROM gateway_log").fetchone()[0]
        finally:
            conn.close()

    def test_requests_without_a_key_each_charge(self):
        self.assertEqual(self.post(500)[0], 201)
        self.assertEqual(self.charges(), 1)


if __name__ == "__main__":
    unittest.main()
