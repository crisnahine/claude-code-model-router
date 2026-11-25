"""Making a backup: the user's workspace folder as a tar, links kept as links, with MANIFEST.json at its root."""
from archive.pack import pack

from . import manifest


def create_backup(workspace, tar_path):
    """Write the backup of workspace to tar_path and return the number of members: each folder, then what it
    holds, by name, and MANIFEST.json last."""
    data = manifest.dumps(manifest.build(workspace)).encode("utf-8")
    return pack(workspace, tar_path, extra=[(manifest.NAME, data)])
