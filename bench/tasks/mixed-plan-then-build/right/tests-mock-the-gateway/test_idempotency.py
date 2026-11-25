import os
import tempfile
import unittest
from unittest import mock

import db
from api.payments import handle_post
from payments import gateway


class IdempotencyKeys(unittest.TestCase):
    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(path)
        self.conn = db.connect(path)
        self.conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        self.conn.commit()
        patcher = mock.patch.object(gateway, "charge", side_effect=lambda conn, cents: f"ch_{cents}")
        self.charge = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, cents, key):
        return handle_post(self.conn, {"headers": {"Idempotency-Key": key}, "body": {"customer_id": 1, "cents": cents}})

    def test_a_retry_is_answered_from_the_first_payment(self):
        first = self.post(500, "k1")
        self.assertEqual(self.post(500, "k1"), first)
        self.assertEqual(self.charge.call_count, 1)

    def test_a_reused_key_is_refused(self):
        self.post(500, "k1")
        self.assertEqual(self.post(700, "k1")[0], 409)
        self.assertEqual(self.charge.call_count, 1)


if __name__ == "__main__":
    unittest.main()
