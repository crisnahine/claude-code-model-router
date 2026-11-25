import unittest

from payments.service import IdempotencyConflict


class Idempotency(unittest.TestCase):
    def test_conflict_is_an_error(self):
        self.assertTrue(issubclass(IdempotencyConflict, Exception))


if __name__ == "__main__":
    unittest.main()
