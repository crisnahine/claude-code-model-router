"""Unpacking a tar archive into a folder without letting it reach outside that folder.

Archives reach us from two places, plugin bundles (plugins/installer.py) and user backups (backups/restore.py), and
neither is trusted. safe_extract unpacks into a staging folder inside the destination, checking each member
against what is really on disk as it goes, checks every link again once all of them exist, and moves the result
into place only when everything passed.
"""
import os
import shutil
import tarfile
import tempfile

from . import limits, paths
from .errors import UnsafeArchive

FILE_MODE = 0o644
EXEC_MODE = 0o755


def safe_extract(tar_path, dest):
    """Extract the tar at tar_path into the existing folder dest; return the member names, in archive order.

    Regular files, folders and symbolic links are extracted, and a link may point only inside dest. A member that
    would land outside dest, a link that points outside it, a name listed twice, any other kind of member or an
    archive over the size limits raises UnsafeArchive, and then nothing has been written."""
    root = os.path.realpath(dest)
    if not os.path.isdir(root):
        raise NotADirectoryError(dest)
    staging = os.path.realpath(tempfile.mkdtemp(prefix=".unpacking-", dir=root))
    try:
        with tarfile.open(tar_path) as tar:
            members = tar.getmembers()
            limits.check(members)
            names = []
            for member in members:
                names.append(place(tar, member, staging, names))
        check_links(staging)
        for entry in os.listdir(staging):
            os.rename(os.path.join(staging, entry), os.path.join(root, entry))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return names


def place(tar, member, staging, earlier):
    name = paths.clean_name(member.name)
    if name in earlier:
        raise UnsafeArchive(f"{name} is listed twice")
    if member.islnk():
        raise UnsafeArchive(f"{name} is a hard link")
    if not (member.isfile() or member.isdir() or member.issym()):
        raise UnsafeArchive(f"{name} is a device, a FIFO or another special file")
    parts = name.split("/")
    parent = os.path.realpath(paths.under(staging, "/".join(parts[:-1])) if len(parts) > 1 else staging)
    if not paths.is_within(staging, parent):
        raise UnsafeArchive(f"{name} would be written outside the destination")
    folder = parent
    while not os.path.lexists(folder):
        folder = os.path.dirname(folder)
    if not os.path.isdir(folder):
        raise UnsafeArchive(f"{name} would be written under a file")
    target = os.path.join(parent, parts[-1])
    if os.path.lexists(target) and not (member.isdir() and os.path.isdir(target)):
        raise UnsafeArchive(f"{name} lands on something already extracted")
    if member.issym():
        if not member.linkname or os.path.isabs(member.linkname):
            raise UnsafeArchive(f"{name} links to an absolute path")
        if not paths.is_within(staging, os.path.realpath(os.path.join(parent, member.linkname))):
            raise UnsafeArchive(f"{name} links to {member.linkname}, outside the destination")
    os.makedirs(parent, exist_ok=True)
    if member.isdir():
        os.makedirs(target, exist_ok=True)
    elif member.issym():
        os.symlink(member.linkname, target)
    else:
        with tar.extractfile(member) as src, open(target, "xb") as out:
            shutil.copyfileobj(src, out)
        os.chmod(target, EXEC_MODE if member.mode & 0o100 else FILE_MODE)
    return name


def check_links(staging):
    """UnsafeArchive when a link, with every other link now in place, leads out of staging."""
    for here, dirs, files in os.walk(staging):
        for entry in dirs + files:
            path = os.path.join(here, entry)
            if os.path.islink(path) and not paths.is_within(staging, os.path.realpath(path)):
                raise UnsafeArchive(f"{os.path.relpath(path, staging)} leads outside the destination")
