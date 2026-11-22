import os

ROOT = "/srv/files"


def serve(name):
    """The bytes of a file under ROOT."""
    path = os.path.join(ROOT, name)
    with open(path, "rb") as fh:
        return fh.read()
