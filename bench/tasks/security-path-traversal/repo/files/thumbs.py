import os

THUMBS = "/srv/thumbs"


def thumb_path(name):
    """Where the thumbnail for name lives; a name that leaves THUMBS is refused."""
    path = os.path.realpath(os.path.join(THUMBS, name))
    if not path.startswith(THUMBS + os.sep):
        raise ValueError("bad name")
    return path
