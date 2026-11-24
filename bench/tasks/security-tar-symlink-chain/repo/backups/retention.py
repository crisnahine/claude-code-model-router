"""How many backups a user keeps: the newest few, plus the newest of each of the last few months."""
import os
import re

NAME = re.compile(r"backup-(\d{4})-(\d{2})-(\d{2})T(\d{6})\.tar")
KEEP_NEWEST = 5
KEEP_MONTHS = 6


def backups(folder):
    """The backup file names in folder, newest first."""
    names = [n for n in os.listdir(folder) if NAME.fullmatch(n)]
    return sorted(names, reverse=True)


def to_keep(names, newest=KEEP_NEWEST, months=KEEP_MONTHS):
    """The names to keep out of names (newest first): the first `newest`, and the newest of each of the first
    `months` months that have any."""
    keep = set(names[:newest])
    seen = []
    for name in names:
        year, month = NAME.fullmatch(name).group(1, 2)
        if (year, month) not in seen:
            seen.append((year, month))
            if len(seen) <= months:
                keep.add(name)
    return keep


def prune(folder, newest=KEEP_NEWEST, months=KEEP_MONTHS):
    """Delete the backups to_keep leaves out; return the names deleted."""
    names = backups(folder)
    keep = to_keep(names, newest, months)
    gone = [n for n in names if n not in keep]
    for name in gone:
        os.remove(os.path.join(folder, name))
    return gone
