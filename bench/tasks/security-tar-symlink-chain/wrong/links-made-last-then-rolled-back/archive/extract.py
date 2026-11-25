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
        made = []
        try:
            # Links go last, so nothing is written through one, and each is checked once all of them exist.
            for member, target in sorted(plan, key=lambda p: p[0].issym()):
                write_member(tar, member, target, made)
            for path in made:
                if os.path.islink(path) and not paths.is_within(root, os.path.realpath(path)):
                    raise UnsafeArchive(f"{os.path.relpath(path, root)} leads outside the destination")
        except (UnsafeArchive, OSError) as e:
            for path in reversed(made):
                if os.path.islink(path) or os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    os.rmdir(path)
            raise UnsafeArchive(str(e)) from None
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
            if not paths.is_within(root, paths.link_target(root, name, member.linkname)):
                raise UnsafeArchive(f"{name} links to {member.linkname}, outside the destination")
        elif member.islnk():
            raise UnsafeArchive(f"{name} is a hard link")
        elif not (member.isfile() or member.isdir()):
            raise UnsafeArchive(f"{name} is a device, a FIFO or another special file")
        plan.append((member, target))
    return plan


def makedirs(path, made):
    missing = []
    while not os.path.lexists(path):
        missing.append(path)
        path = os.path.dirname(path)
    for folder in reversed(missing):
        os.mkdir(folder)
        made.append(folder)


def write_member(tar, member, target, made):
    if member.isdir():
        makedirs(target, made)
        return
    makedirs(os.path.dirname(target), made)
    if member.issym():
        os.symlink(member.linkname, target)
        made.append(target)
        return
    made.append(target)
    with tar.extractfile(member) as src, open(target, "xb") as out:
        shutil.copyfileobj(src, out)
    os.chmod(target, EXEC_MODE if member.mode & 0o100 else FILE_MODE)
