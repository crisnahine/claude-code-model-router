import os
import tempfile
import unittest

import api.payments
import db


class IdempotencyKeys(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(self.path)
        with db.connect(self.path) as conn:
            conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")

    def post(self, cents, key=None):
        conn = db.connect(self.path)
        try:
            headers = {"idempotency-key": key} if key else {}
            return api.payments.handle_post(conn, {"headers": headers, "body": {"customer_id": 1, "cents": cents}})
        finally:
            conn.close()

    def charges(self):
        conn = db.connect(self.path)
        try:
            return conn.execute("SELECT COUNT(*) FROM gateway_log").fetchone()[0]
        finally:
            conn.close()

    def test_reused_key_other_body_is_409(self):
        self.post(500, "k1")
        self.assertEqual(self.post(700, "k1"), (409, {"error": "idempotency key reused"}))

    def test_retry_replays(self):
        first = self.post(500, "k1")
        self.assertEqual(self.post(500, "k1"), first)
        self.assertEqual(self.charges(), 1)
