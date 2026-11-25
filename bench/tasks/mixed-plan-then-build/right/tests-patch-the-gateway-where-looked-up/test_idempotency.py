import itertools
import os
import tempfile
import unittest
from unittest import mock

import db
from api.payments import handle_post


class IdempotencyKeys(unittest.TestCase):
    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(), "shop.db")
        db.init(path)
        self.conn = db.connect(path)
        self.conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        self.conn.commit()
        ids = itertools.count(1)
        patcher = mock.patch("payments.service.gateway")
        self.gateway = patcher.start()
        self.gateway.charge.side_effect = lambda conn, cents: f"ch_{next(ids)}"
        self.addCleanup(patcher.stop)

    def post(self, cents, key):
        return handle_post(self.conn, {"headers": {"Idempotency-Key": key}, "body": {"customer_id": 1, "cents": cents}})

    def test_a_retry_is_answered_from_the_first_payment_and_charged_once(self):
        first = self.post(500, "k1")
        self.assertEqual(first[0], 201)
        self.assertEqual(self.post(500, "k1"), first)
        self.assertEqual(self.gateway.charge.call_count, 1)

    def test_a_reused_key_is_refused_and_not_charged(self):
        self.post(500, "k1")
        self.assertEqual(self.post(700, "k1")[0], 409)
        self.assertEqual(self.gateway.charge.call_count, 1)

    def test_a_new_key_is_charged(self):
        self.assertEqual(self.post(500, "k1")[0], 201)
        self.assertEqual(self.post(500, "k2")[0], 201)
        self.assertEqual(self.gateway.charge.call_count, 2)


if __name__ == "__main__":
    unittest.main()
