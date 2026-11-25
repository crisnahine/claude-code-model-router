"""Path rules for archive members. A member's name is a POSIX path relative to the archive's root."""
import os
import posixpath

from .errors import UnsafeArchive

MAX_NAME = 1024


def clean_name(name):
    """The member name as a relative path with no empty or "." parts. UnsafeArchive for an absolute name, a drive
    letter, a ".." part, a backslash or a NUL."""
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise UnsafeArchive(f"bad member name {name!r}")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise UnsafeArchive(f"{name} is an absolute path")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts:
        raise UnsafeArchive(f"bad member name {name!r}")
    if ".." in parts:
        raise UnsafeArchive(f"{name} climbs out with ..")
    clean = "/".join(parts)
    if len(clean) > MAX_NAME:
        raise UnsafeArchive(f"a member name is longer than {MAX_NAME} characters")
    return clean


def is_within(root, path):
    """Whether path is root or under it; both absolute and normalized."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def link_target(root, name, linkname):
    """Where a link called name and pointing at linkname leads, as an absolute path, from the archive's names."""
    return os.path.normpath(os.path.join(root, posixpath.dirname(name), linkname))


def under(root, name):
    """The absolute path a member name maps to under root."""
    return os.path.join(root, *name.split("/"))
