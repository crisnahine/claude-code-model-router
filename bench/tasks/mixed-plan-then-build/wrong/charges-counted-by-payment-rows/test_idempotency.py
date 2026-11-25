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

    def payment_rows(self):
        conn = db.connect(self.path)
        try:
            return conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        finally:
            conn.close()

    def test_a_retry_gets_the_first_response_and_no_second_charge(self):
        first = self.post(500, "k1")
        self.assertEqual(first[0], 201)
        self.assertEqual(self.post(500, "k1"), first)
        self.assertEqual(self.payment_rows(), 1)

    def test_a_reused_key_with_another_amount_is_refused(self):
        self.post(500, "k1")
        self.assertEqual(self.post(700, "k1"), (409, {"error": "idempotency key reused"}))
        self.assertEqual(self.payment_rows(), 1)

    def test_requests_without_a_key_each_charge(self):
        self.assertNotEqual(self.post(500), self.post(500))
        self.assertEqual(self.payment_rows(), 2)


if __name__ == "__main__":
    unittest.main()
