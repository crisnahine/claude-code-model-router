import unittest

from shipit.uploads import accepts


class Uploads(unittest.TestCase):
    def test_an_empty_upload_is_refused(self):
        self.assertFalse(accepts(0))

    def test_a_small_upload_is_accepted(self):
        self.assertTrue(accepts(1024))


if __name__ == "__main__":
    unittest.main()
