import hashlib
import hmac
import os
import secrets
from collections import namedtuple

KEY = os.environ.get("SESSION_KEY", "").encode() or secrets.token_bytes(32)
ADMINS = {"alice"}
User = namedtuple("User", "name is_admin")


def sign(name):
    return hmac.new(KEY, name.encode(), hashlib.sha256).hexdigest()


def issue(name):
    return f"{name}.{sign(name)}"


def user(cookie):
    """The user a cookie was issued to, or None."""
    name, _, sig = cookie.rpartition(".")
    if not name or not hmac.compare_digest(sig, sign(name)):
        return None
    return User(name, name in ADMINS)
