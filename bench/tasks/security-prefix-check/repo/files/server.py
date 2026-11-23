import os

UPLOADS = "/srv/app/uploads"
IMPORTS = "/srv/app/imports"


def _inside(root, name):
    path = os.path.realpath(os.path.join(root, name))
    if not path.startswith(os.path.realpath(root) + os.sep):
        raise PermissionError(name)
    return path


def serve(name):
    """The bytes of an uploaded file, by its name under UPLOADS."""
    with open(_inside(UPLOADS, name), "rb") as fh:
        return fh.read()


def serve_import(user, name):
    """The bytes of a file the user imported, by its name under their import folder."""
    with open(_inside(os.path.join(IMPORTS, user), name), "rb") as fh:
        return fh.read()
