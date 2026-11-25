import os
import tempfile
import unittest
from unittest import mock

import db
from api.payments import handle_post
from payments import gateway


class IdempotencyKeys(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(self.path)
        conn = db.connect(self.path)
        conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        conn.commit()
        conn.close()
        patcher = mock.patch.object(gateway, "charge", wraps=gateway.charge)
        self.charge = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, cents, key):
        conn = db.connect(self.path)
        try:
            return handle_post(conn, {"headers": {"Idempotency-Key": key}, "body": {"customer_id": 1, "cents": cents}})
        finally:
            conn.close()

    def test_repeats_charge_once_and_reuse_is_refused(self):
        self.post(500, "k1")
        self.post(500, "k1")
        self.assertEqual(self.charge.call_count, 1)
        self.assertEqual(self.post(700, "k1"), (409, {"error": "idempotency key reused"}))
        self.assertEqual(self.charge.call_count, 1)


if __name__ == "__main__":
    unittest.main()
