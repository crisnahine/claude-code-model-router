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


MAX_HOPS = 40


def resolve(parts, links):
    """Where a path lands once every link the archive makes is followed: its parts under the root, or None when
    it leaves the root or loops. links maps each link's real location to its target."""
    out, queue, hops = [], list(parts), 0
    while queue:
        part = queue.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not out:
                return None
            out.pop()
            continue
        out.append(part)
        target = links.get("/".join(out))
        if target is not None:
            hops += 1
            if hops > MAX_HOPS:
                return None
            out.pop()  # a link's target is relative to the folder holding the link
            queue = target.split("/") + queue
    return out


def plan_members(root, members):
    """[(member, the absolute path it goes to), ...] once every member has passed, or UnsafeArchive.

    Names alone don't say where a member lands: a link the archive made earlier can sit in the middle of a later
    name or target. So each member is placed by following the archive's own links, and once every link is known,
    each one is followed again, since a later link can change where an earlier one points. A member that would land
    under a file, or on a folder an earlier member needed, is refused here rather than failing halfway through."""
    seen, dirs, links, plan = set(), set(), {}, []
    for member in members:
        name = paths.clean_name(member.name)
        parts = name.split("/")
        parent = resolve(parts[:-1], links)
        if parent is None:
            raise UnsafeArchive(f"{name} would be written outside the destination")
        folders = ["/".join(parent[:n]) for n in range(1, len(parent) + 1)]
        if any(folder in seen and folder not in dirs for folder in folders):
            raise UnsafeArchive(f"{name} would be written under a file")
        real = "/".join(parent + parts[-1:])
        if real in seen:
            raise UnsafeArchive(f"{name} is listed twice")
        if real in dirs and not member.isdir():
            raise UnsafeArchive(f"{name} lands on a folder the archive already made")
        seen.add(real)
        dirs.update(folders)
        if member.isdir():
            dirs.add(real)
        target = paths.under(root, real)
        # realpath also catches a link that was already in dest before this archive.
        if not paths.is_within(root, os.path.realpath(target)):
            raise UnsafeArchive(f"{name} would be written outside the destination")
        if member.issym():
            if not member.linkname or os.path.isabs(member.linkname):
                raise UnsafeArchive(f"{name} links to an absolute path")
            if resolve(parent + member.linkname.split("/"), links) is None:
                raise UnsafeArchive(f"{name} links to {member.linkname}, outside the destination")
            links[name] = member.linkname
        elif member.islnk():
            raise UnsafeArchive(f"{name} is a hard link")
        elif not (member.isfile() or member.isdir()):
            raise UnsafeArchive(f"{name} is a device, a FIFO or another special file")
        plan.append((member, target))
    for real, linkname in links.items():
        if resolve(real.split("/")[:-1] + linkname.split("/"), links) is None:
            raise UnsafeArchive(f"{real} links to {linkname}, which leads outside the destination")
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
