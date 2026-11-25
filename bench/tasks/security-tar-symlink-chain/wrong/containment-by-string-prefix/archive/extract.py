"""Unpacking a tar archive into a folder without letting it reach outside that folder.

Archives reach us from two places, plugin bundles (plugins/installer.py) and user backups (backups/restore.py), and
neither is trusted. safe_extract checks each member against what is on disk as it writes it, checks every link
again at the end, and removes everything it made when anything fails.
"""
import os
import shutil
import tarfile

from . import limits, paths
from .errors import UnsafeArchive

FILE_MODE = 0o644
EXEC_MODE = 0o755


def inside(root, path):
    return os.path.realpath(path).startswith(root)


def safe_extract(tar_path, dest):
    """Extract the tar at tar_path into the existing folder dest; return the member names, in archive order."""
    root = os.path.realpath(dest)
    if not os.path.isdir(root):
        raise NotADirectoryError(dest)
    made = []
    names = []
    try:
        with tarfile.open(tar_path) as tar:
            members = tar.getmembers()
            limits.check(members)
            for member in members:
                name = paths.clean_name(member.name)
                if name in names:
                    raise UnsafeArchive(f"{name} is listed twice")
                if member.islnk():
                    raise UnsafeArchive(f"{name} is a hard link")
                if not (member.isfile() or member.isdir() or member.issym()):
                    raise UnsafeArchive(f"{name} is a device, a FIFO or another special file")
                target = paths.under(root, name)
                parent = os.path.dirname(target)
                if not inside(root, parent):
                    raise UnsafeArchive(f"{name} would be written outside the destination")
                if os.path.lexists(target) and not member.isdir():
                    raise UnsafeArchive(f"{name} lands on something already extracted")
                if member.issym():
                    if not member.linkname or os.path.isabs(member.linkname):
                        raise UnsafeArchive(f"{name} links to an absolute path")
                    if not inside(root, os.path.join(os.path.realpath(parent), member.linkname)):
                        raise UnsafeArchive(f"{name} links to {member.linkname}, outside the destination")
                missing = []
                p = os.path.realpath(parent)
                while not os.path.lexists(p):
                    missing.append(p)
                    p = os.path.dirname(p)
                os.makedirs(os.path.realpath(parent), exist_ok=True)
                made.extend(reversed(missing))
                real = os.path.join(os.path.realpath(parent), os.path.basename(target))
                if member.isdir():
                    if not os.path.isdir(real):
                        os.mkdir(real)
                        made.append(real)
                elif member.issym():
                    os.symlink(member.linkname, real)
                    made.append(real)
                else:
                    with tar.extractfile(member) as src, open(real, "xb") as out:
                        shutil.copyfileobj(src, out)
                    made.append(real)
                    os.chmod(real, EXEC_MODE if member.mode & 0o100 else FILE_MODE)
                names.append(name)
        for path in made:
            if os.path.islink(path) and not inside(root, path):
                raise UnsafeArchive(f"{path} leads outside the destination")
    except BaseException:
        for path in reversed(made):
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                os.rmdir(path)
        raise
    return names
