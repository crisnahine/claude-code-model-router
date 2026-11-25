import os
import tempfile
import unittest

import db
from api.payments import handle_post
from payments.service import IdempotencyConflict, create_payment


class Keys(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(self.path)
        self.conn = db.connect(self.path)
        self.conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def post(self, cents, key):
        return handle_post(self.conn, {"headers": {"idempotency-key": key}, "body": {"customer_id": 1, "cents": cents}})

    def charged(self):
        return self.conn.execute("SELECT COUNT(*) FROM gateway_log").fetchone()[0]

    def test_the_same_request_twice_charges_once(self):
        first = self.post(900, "abc")
        self.assertEqual((first, self.charged()), (self.post(900, "abc"), 1))

    def test_another_amount_on_a_used_key_is_a_conflict(self):
        self.post(900, "abc")
        self.assertEqual(self.post(901, "abc")[0], 409)
        with self.assertRaises(IdempotencyConflict):
            create_payment(self.conn, 1, 902, "abc")
        self.assertEqual(self.charged(), 1)


if __name__ == "__main__":
    unittest.main()
