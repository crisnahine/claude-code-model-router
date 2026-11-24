import unittest

from shop.http.router import Request, Router


class Routing(unittest.TestCase):
    def test_params_and_method(self):
        router = Router()
        router.add("GET", "/orders/<id>", lambda r: (200, r.params))
        self.assertEqual(router.dispatch(Request("GET", "/orders/7")), (200, {"id": "7"}))
        self.assertEqual(router.dispatch(Request("POST", "/orders/7"))[0], 404)
