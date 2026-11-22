import io
import os
import tarfile
from pathlib import PurePosixPath

from files.server import IMPORTS


def import_bundle(user, data):
    """Unpack a user's .tar bundle into their own folder under IMPORTS and list what it holds."""
    dest = os.path.join(IMPORTS, user)
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in PurePosixPath(member.name).parts:
                raise ValueError(f"unsafe path in bundle: {member.name}")
        tar.extractall(dest)
    return sorted(os.listdir(dest))
