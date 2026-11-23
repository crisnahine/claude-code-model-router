import os

ROOT = os.path.realpath(os.environ.get("FILES_ROOT", "/srv/files"))


def read(name):
    path = os.path.realpath(os.path.join(ROOT, name))
    if os.path.commonpath([ROOT, path]) != ROOT:
        raise PermissionError(name)
    with open(path, "rb") as fh:
        return fh.read()
