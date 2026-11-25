import os
import tempfile
import unittest

import db
from api.payments import handle_post


class Payments(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.path = os.path.join(folder, "shop.db")
        db.init(self.path)
        self.conn = db.connect(self.path)
        self.conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def charges(self):
        return self.conn.execute("SELECT COUNT(*) FROM gateway_log").fetchone()[0]

    def test_a_payment_is_charged_and_recorded(self):
        status, body = handle_post(self.conn, {"headers": {}, "body": {"customer_id": 1, "cents": 1250}})
        self.assertEqual(status, 201)
        self.assertEqual((body["customer_id"], body["cents"]), (1, 1250))
        self.assertEqual(self.charges(), 1)

    def test_a_bad_body_is_refused_without_a_charge(self):
        self.assertEqual(handle_post(self.conn, {"headers": {}, "body": {"customer_id": 1}})[0], 400)
        self.assertEqual(handle_post(self.conn, {"headers": {}, "body": {"customer_id": 1, "cents": -5}})[0], 400)
        self.assertEqual(self.charges(), 0)

    def test_an_unknown_customer_is_not_charged(self):
        self.assertEqual(handle_post(self.conn, {"headers": {}, "body": {"customer_id": 9, "cents": 100}})[0], 404)
        self.assertEqual(self.charges(), 0)

    def test_a_declined_card_records_nothing(self):
        status, _ = handle_post(self.conn, {"headers": {}, "body": {"customer_id": 1, "cents": 2_000_000}})
        self.assertEqual(status, 402)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
