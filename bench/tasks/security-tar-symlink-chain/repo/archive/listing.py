"""What an archive holds, for the admin page that shows an upload before anyone installs or restores it."""
import tarfile

KINDS = {tarfile.REGTYPE: "file", tarfile.AREGTYPE: "file", tarfile.DIRTYPE: "folder", tarfile.SYMTYPE: "link",
         tarfile.LNKTYPE: "hard link", tarfile.FIFOTYPE: "fifo", tarfile.CHRTYPE: "device", tarfile.BLKTYPE: "device"}


def describe(tar_path):
    """[{"name", "kind", "size", "target"}, ...] in archive order. Reads the headers only."""
    out = []
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            out.append({"name": member.name, "kind": KINDS.get(member.type, "other"), "size": member.size,
                        "target": member.linkname or None})
    return out


def summary(entries):
    """"12 files (3.4 KB), 3 folders, 2 links" for the page's heading."""
    counts = {}
    for e in entries:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    size = sum(e["size"] for e in entries if e["kind"] == "file")
    parts = []
    for kind, label in (("file", "file"), ("folder", "folder"), ("link", "link"), ("hard link", "hard link")):
        n = counts.get(kind, 0)
        if n:
            text = f"{n} {label}{'s' if n != 1 else ''}"
            parts.append(f"{text} ({_size(size)})" if kind == "file" else text)
    others = sum(n for k, n in counts.items() if k not in ("file", "folder", "link", "hard link"))
    if others:
        parts.append(f"{others} other")
    return ", ".join(parts) or "empty"


def _size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
