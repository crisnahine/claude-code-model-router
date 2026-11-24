"""Size limits, so a small archive can't fill the disk or tie up a worker."""
from .errors import UnsafeArchive

MAX_MEMBERS = 20_000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def check(members):
    if len(members) > MAX_MEMBERS:
        raise UnsafeArchive(f"{len(members)} members, more than {MAX_MEMBERS}")
    total = 0
    for member in members:
        if member.isfile():
            if member.size > MAX_FILE_BYTES:
                raise UnsafeArchive(f"{member.name} is {member.size} bytes, more than {MAX_FILE_BYTES}")
            total += member.size
    if total > MAX_TOTAL_BYTES:
        raise UnsafeArchive(f"{total} bytes in all, more than {MAX_TOTAL_BYTES}")
