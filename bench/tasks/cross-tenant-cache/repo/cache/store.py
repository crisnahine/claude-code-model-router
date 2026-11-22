_DATA = {}


def get(key):
    return _DATA.get(key)


def set(key, value):
    _DATA[key] = value


def delete(key):
    _DATA.pop(key, None)


def keys():
    return list(_DATA)


def clear():
    _DATA.clear()
