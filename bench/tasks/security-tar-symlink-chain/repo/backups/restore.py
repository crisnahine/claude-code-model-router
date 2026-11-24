"""Restoring a backup a user uploaded."""
import json
import os
import shutil

from archive import UnsafeArchive, safe_extract

from . import manifest

MARKER = ".restored"


def restore_backup(tar_path, target):
    """Restore the backup at tar_path into target, a folder that must not exist yet, and check it against its
    MANIFEST.json. Return the number of members restored.

    An unsafe archive raises UnsafeArchive and a backup that doesn't match its manifest raises
    manifest.ManifestMismatch; either way no target folder is left behind."""
    if os.path.lexists(target):
        raise FileExistsError(target)
    os.mkdir(target)
    try:
        names = safe_extract(tar_path, target)
    except UnsafeArchive:
        os.rmdir(target)  # safe_extract wrote nothing, so the folder is still empty
        raise
    try:
        with open(os.path.join(target, manifest.NAME), encoding="utf-8") as f:
            listed = json.load(f)
        manifest.verify(target, listed)
    except (OSError, ValueError) as e:
        shutil.rmtree(target)
        if isinstance(e, manifest.ManifestMismatch):
            raise
        raise manifest.ManifestMismatch(f"the backup's {manifest.NAME} can't be read: {e}") from None
    with open(os.path.join(target, MARKER), "w", encoding="utf-8") as f:
        f.write(f"{len(names)} members\n")
    return len(names)
