import posixpath
from urllib.parse import unquote

PROTECTED = ("/admin/", "/internal/")


def allowed(user, raw):
    """Whether this user may request this path."""
    if user is None:
        return False
    path = posixpath.normpath("/" + unquote(raw.split("?", 1)[0]).lstrip("/")) + "/"
    if path.startswith(PROTECTED):
        return user.is_admin
    return True
