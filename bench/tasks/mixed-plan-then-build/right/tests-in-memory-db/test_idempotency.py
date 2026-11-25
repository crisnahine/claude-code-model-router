import os
import sqlite3
import tempfile
import unittest

import db
from api.payments import handle_post


class IdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(open(db.SCHEMA, encoding="utf-8").read())
        self.conn.execute("INSERT INTO customers (id, email) VALUES (1, 'a@example.com'), (2, 'b@example.com')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def post(self, customer, cents, key=None):
        headers = {"Idempotency-Key": key} if key else {}
        return handle_post(self.conn, {"headers": headers, "body": {"customer_id": customer, "cents": cents}})

    def charges(self):
        return self.conn.execute("SELECT COUNT(*) FROM gateway_log").fetchone()[0]

    def test_different_body_with_same_key_is_rejected(self):
        status, _ = self.post(1, 500, "key-123")
        self.assertEqual(status, 201)
        self.assertEqual(self.post(1, 900, "key-123"), (409, {"error": "idempotency key reused"}))
        self.assertEqual(self.charges(), 1)

    def test_no_key_charges_each_time(self):
        a, b = self.post(1, 500), self.post(1, 500)
        self.assertEqual((a[0], b[0]), (201, 201))
        self.assertNotEqual(a[1]["id"], b[1]["id"])
        self.assertEqual(self.charges(), 2)

    def test_same_key_and_body_replays_first_response(self):
        first = self.post(1, 500, "key-123")
        second = self.post(1, 500, "key-123")
        self.assertEqual(first[0], 201)
        self.assertEqual(second, first)
        self.assertEqual(self.charges(), 1)


if __name__ == '__main__':
    unittest.main()
