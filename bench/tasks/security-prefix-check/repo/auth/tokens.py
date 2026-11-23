import hashlib
import hmac
import os
import re
import secrets

KEY = os.environ.get("TOKEN_KEY", "").encode() or secrets.token_bytes(32)
USER = re.compile(r"[a-z0-9]{3,20}")


def issue(user):
    if not USER.fullmatch(user):
        raise ValueError(user)
    sig = hmac.new(KEY, user.encode(), hashlib.sha256).hexdigest()
    return f"{user}.{sig}"


def user(token):
    """The user a token was issued to, or None."""
    name, _, sig = token.rpartition(".")
    expected = hmac.new(KEY, name.encode(), hashlib.sha256).hexdigest()
    return name if USER.fullmatch(name) and hmac.compare_digest(sig, expected) else None
