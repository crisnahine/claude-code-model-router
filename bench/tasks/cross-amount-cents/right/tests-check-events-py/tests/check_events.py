from shop import events


def test_upcast():
    assert events.upcast([{"amount": 0.29}])
