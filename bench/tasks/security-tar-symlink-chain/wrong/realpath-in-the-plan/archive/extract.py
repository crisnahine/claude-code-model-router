"""Unpacking a tar archive into a folder without letting it reach outside that folder.

Archives reach us from two places, plugin bundles (plugins/installer.py) and user backups (backups/restore.py), and
neither is trusted. safe_extract checks every member before it writes anything, so an archive it refuses leaves
the destination as it was.
"""
import os
import shutil
import tarfile

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
    with tarfile.open(tar_path) as tar:
        members = tar.getmembers()
        limits.check(members)
        plan = plan_members(root, members)
        for member, target in plan:
            write_member(tar, member, target)
    return [paths.clean_name(m.name) for m in members]


def plan_members(root, members):
    """[(member, the absolute path it goes to), ...] once every member has passed, or UnsafeArchive."""
    seen = set()
    plan = []
    for member in members:
        name = paths.clean_name(member.name)
        if name in seen:
            raise UnsafeArchive(f"{name} is listed twice")
        seen.add(name)
        target = paths.under(root, name)
        if not paths.is_within(root, os.path.realpath(target)):
            raise UnsafeArchive(f"{name} would be written outside the destination")
        if member.issym():
            if not member.linkname or os.path.isabs(member.linkname):
                raise UnsafeArchive(f"{name} links to an absolute path")
            if not paths.is_within(root, os.path.realpath(paths.link_target(root, name, member.linkname))):
                raise UnsafeArchive(f"{name} links to {member.linkname}, outside the destination")
        elif member.islnk():
            raise UnsafeArchive(f"{name} is a hard link")
        elif not (member.isfile() or member.isdir()):
            raise UnsafeArchive(f"{name} is a device, a FIFO or another special file")
        plan.append((member, target))
    return plan


def write_member(tar, member, target):
    if member.isdir():
        os.makedirs(target, exist_ok=True)
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if member.issym():
        os.symlink(member.linkname, target)
        return
    with tar.extractfile(member) as src, open(target, "xb") as out:
        shutil.copyfileobj(src, out)
    os.chmod(target, EXEC_MODE if member.mode & 0o100 else FILE_MODE)
