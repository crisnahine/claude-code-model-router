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
        conn = db.connect(self.path)
        try:
            return handle_post(conn, {"headers": {"Idempotency-Key": key} if key else {},
                                      "body": {"customer_id": 1, "cents": cents}})
        finally:
            conn.close()

    def test_a_retry_gets_the_first_response(self):
        first = self.post(500, "k1")
        self.assertEqual(first[0], 201)
        self.assertEqual(self.post(500, "k1"), first)

    def test_a_reused_key_with_another_amount_is_refused(self):
        self.post(500, "k1")
        self.assertEqual(self.post(700, "k1"), (409, {"error": "idempotency key reused"}))

    def test_requests_without_a_key_are_new_payments(self):
        self.assertNotEqual(self.post(500), self.post(500))


if __name__ == "__main__":
    unittest.main()
