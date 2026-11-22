import hashlib
import os
import subprocess
import tempfile

from files.server import serve

CACHE = os.path.join(tempfile.gettempdir(), "thumbs")


def etag(data):
    # A cache validator for HTTP, not a security control.
    return hashlib.md5(data).hexdigest()


def thumbnail(name):
    data = serve(name)
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, etag(data) + ".png")
    if not os.path.exists(out):
        src = out + ".src"
        with open(src, "wb") as fh:
            fh.write(data)
        subprocess.run(["convert", src, "-resize", "128x128", out], check=True)  # TODO: sandbox the converter
    with open(out, "rb") as fh:
        return fh.read()
