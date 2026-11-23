import hashlib
import hmac
import pickle
import secrets

KEY = secrets.token_bytes(32)


def sign(payload):
    return hmac.new(KEY, payload, hashlib.sha256).hexdigest()


def receive(payload, signature):
    if not hmac.compare_digest(signature, sign(payload)):
        raise PermissionError("bad signature")
    return pickle.loads(payload)
