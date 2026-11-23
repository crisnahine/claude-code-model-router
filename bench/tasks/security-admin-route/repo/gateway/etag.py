import hashlib


def etag(data):
    # A cache validator, not a security control.
    return hashlib.md5(data).hexdigest()
