"""Packing a folder into a tar, the way the plugin SDK builds bundles and the app builds backups."""
import io
import os
import tarfile

SKIP = {"__pycache__", ".git", ".DS_Store"}


def walk(folder):
    """(relative POSIX path, absolute path) for everything under folder: each folder before what it holds, names
    sorted, links not followed, SKIP names left out."""
    for top, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(dirs + [n for n in names if n not in SKIP]):
            path = os.path.join(top, name)
            yield os.path.relpath(path, folder).replace(os.sep, "/"), path


def pack(folder, tar_path, extra=()):
    """Write folder to tar_path, plus extra (arcname, bytes) files at the end; return the member count. Owners
    are cleared so the same folder always packs to the same members."""
    count = 0
    with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as tar:
        for rel, path in walk(folder):
            info = tar.gettarinfo(path, arcname=rel)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            if info.isfile():
                with open(path, "rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)
            count += 1
        for arcname, data in extra:
            info = tarfile.TarInfo(arcname)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
            count += 1
    return count
