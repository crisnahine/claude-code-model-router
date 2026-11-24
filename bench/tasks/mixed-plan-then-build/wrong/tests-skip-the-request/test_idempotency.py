import os
import tempfile
import unittest

import db
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

    def test_a_repeated_key_returns_the_first_payment(self):
        first = create_payment(self.conn, 1, 900, "abc")
        self.assertEqual(create_payment(self.conn, 1, 900, "abc"), first)

    def test_another_amount_on_a_used_key_is_a_conflict(self):
        create_payment(self.conn, 1, 900, "abc")
        with self.assertRaises(IdempotencyConflict):
            create_payment(self.conn, 1, 901, "abc")


if __name__ == "__main__":
    unittest.main()
