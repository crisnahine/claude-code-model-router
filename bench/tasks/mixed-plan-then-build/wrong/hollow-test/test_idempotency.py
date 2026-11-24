import unittest

from payments.service import KeyReused


class Idempotency(unittest.TestCase):
    def test_key_reused_is_an_error(self):
        self.assertTrue(issubclass(KeyReused, Exception))


if __name__ == "__main__":
    unittest.main()
