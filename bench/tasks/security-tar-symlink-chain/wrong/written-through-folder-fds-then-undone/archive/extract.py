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
        top = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        made = []
        try:
            for member, target in plan:
                write_member(tar, member, os.path.relpath(target, root).split(os.sep), top, made)
            # Each name is checked where it really landed once every link exists.
            for member, target in plan:
                if not paths.is_within(root, os.path.realpath(target)):
                    raise UnsafeArchive(f"{member.name} landed outside the destination")
        except BaseException:
            for fd, name, is_dir in reversed(made):
                os.rmdir(name, dir_fd=fd) if is_dir else os.remove(name, dir_fd=fd)
            raise
        finally:
            for fd, _, _ in made:
                try:
                    os.close(fd)
                except OSError:
                    pass
            os.close(top)
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


def _folder(top, parts, made):
    """An fd for the folder parts under top, made as needed."""
    fd = os.dup(top)
    for part in parts:
        try:
            os.mkdir(part, 0o755, dir_fd=fd)
            made.append((os.dup(fd), part, True))
        except FileExistsError:
            pass
        nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
        os.close(fd)
        fd = nxt
    return fd


def write_member(tar, member, parts, top, made):
    if member.isdir():
        os.close(_folder(top, parts, made))
        return
    fd = _folder(top, parts[:-1], made)
    if member.issym():
        os.symlink(member.linkname, parts[-1], dir_fd=fd)
    else:
        mode = EXEC_MODE if member.mode & 0o100 else FILE_MODE
        out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
        with tar.extractfile(member) as src, os.fdopen(out, "wb") as f:
            shutil.copyfileobj(src, f)
            os.fchmod(f.fileno(), mode)
    made.append((fd, parts[-1], False))
