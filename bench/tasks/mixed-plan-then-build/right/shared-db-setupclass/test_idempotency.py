import os
import tempfile
import unittest

import db
from api.payments import handle_post


class IdempotencyKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(cls.path)
        conn = db.connect(cls.path)
        conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        conn.commit()
        conn.close()

    def post(self, cents, key=None):
        conn = db.connect(self.path)
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

    def test_retry(self):
        before = self.charges()
        first = self.post(500, "retry-key")
        self.assertEqual(first[0], 201)
        self.assertEqual(self.post(500, "retry-key"), first)
        self.assertEqual(self.charges(), before + 1)

    def test_reuse(self):
        self.post(500, "reuse-key")
        before = self.charges()
        self.assertEqual(self.post(900, "reuse-key"), (409, {"error": "idempotency key reused"}))
        self.assertEqual(self.charges(), before)
