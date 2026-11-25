import builtins
import fcntl
import hashlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

# Set before the work dir's code is imported, so its imports write no bytecode the hook would count.
sys.dont_write_bytecode = True
passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)
# event: [(the position of a path it writes, the position of the dir_fd that path is relative to, or None), ...]
WRITES = {"os.mkdir": [(0, 2)], "os.rmdir": [(0, 1)], "os.remove": [(0, 1)], "os.symlink": [(1, 2)],
          "os.link": [(1, 3)], "os.rename": [(0, 2), (1, 3)], "os.chmod": [(0, 2)], "os.chown": [(0, 3)],
          "os.utime": [(0, 3)], "os.truncate": [(0, None)], "os.mkfifo": [(0, 2)], "os.mknod": [(0, 3)],
          "shutil.rmtree": [(0, 1)]}
FOLLOWED = {"os.chmod", "os.chown", "os.utime", "os.truncate"}
OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def fd_folder(fd):
    """The path of the folder an open fd refers to."""
    if hasattr(fcntl, "F_GETPATH"):
        return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0")[0])
    return os.readlink(f"/proc/self/fd/{fd}")


def landing(path, follow, dir_fd=None):
    """Where a write to path really lands, following the links already on disk."""
    path = os.fsdecode(path)
    if dir_fd is not None and dir_fd >= 0 and not os.path.isabs(path):
        path = os.path.join(fd_folder(dir_fd), path)
    path = os.path.abspath(path)
    if follow:
        return os.path.realpath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def watcher():
    """An audit hook that records each write the task code makes outside the folder it was given, and each link in
    that folder that leads out of it when a write is made, even when the code undoes them before it returns, and a
    function that sets that folder (None stops recording and returns both lists). It can't see writes made by a
    child process."""
    state = {"top": None, "seen": [], "leaked": [], "dir_fd": None, "opener": False}

    def hook(event, args):
        top = state["top"]
        if top is None:
            return
        try:
            places = landings(event, args)
        except Exception:
            places = ["?"]
        for place in places:
            if not inside(top, place):
                state["seen"].append(f"{event} {os.path.relpath(place, top) if place != '?' else args!r}"[:200])
        if places:
            leaks(top)

    def inside(top, path):
        return path == top or path.startswith(top + os.sep)

    def leaks(top):
        """The links under top that lead out of it now, before the write the hook was called for."""
        for here, dirs, files in os.walk(top):
            for name in dirs + files:
                path = os.path.join(here, name)
                if os.path.islink(path) and not inside(top, os.path.realpath(path)):
                    found = f"{os.path.relpath(path, top)} -> {os.readlink(path)}"
                    if found not in state["leaked"]:
                        state["leaked"].append(found)

    def landings(event, args):
        if event == "open":
            path, mode, flags = args
            # open() with an opener names the file before the opener's own os.open, which is the one checked.
            if isinstance(path, int) or not flags & OPEN_FLAGS or (mode is not None and state["opener"]):
                return []
            return [landing(path, not flags & os.O_NOFOLLOW, state["dir_fd"] if mode is None else None)]
        out = []
        for at, fd in WRITES.get(event, []):
            if isinstance(args[at], int):
                continue
            dir_fd = args[fd] if fd is not None and fd < len(args) and isinstance(args[fd], int) else None
            out.append(landing(args[at], event in FOLLOWED, dir_fd))
        return out

    real_os_open, real_io_open = os.open, io.open

    # The audit event for os.open leaves out dir_fd, so the call itself records it.
    def os_open(path, flags, mode=0o777, *, dir_fd=None):
        state["dir_fd"] = dir_fd
        try:
            return real_os_open(path, flags, mode, dir_fd=dir_fd)
        finally:
            state["dir_fd"] = None

    def io_open(file, *args, **kwargs):
        state["opener"] = kwargs.get("opener", args[6] if len(args) > 6 else None) is not None
        try:
            return real_io_open(file, *args, **kwargs)
        finally:
            state["opener"] = False

    def watch(top):
        state["top"] = top and os.path.realpath(top)
        found = state["seen"], state["leaked"]
        state["seen"], state["leaked"] = [], []
        return found

    os.open, io.open, builtins.open = os_open, io_open, io_open
    sys.addaudithook(hook)
    return watch


watch = watcher()
TYPES = {"dir": (tarfile.DIRTYPE, 0o755), "link": (tarfile.SYMTYPE, 0o777), "hard": (tarfile.LNKTYPE, 0o644),
         "fifo": (tarfile.FIFOTYPE, 0o644)}


def make_tar(path, members):
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tar:
        for kind, name, *rest in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = rest[0].encode("utf-8")
                info.size, info.mode = len(data), rest[1] if len(rest) > 1 else 0o644
                tar.addfile(info, io.BytesIO(data))
            else:
                info.type, info.mode = TYPES[kind]
                info.linkname = rest[0] if rest else ""
                tar.addfile(info)


def tree(top):
    out = {}
    for here, dirs, files in os.walk(top):
        for name in dirs + files:
            path = os.path.join(here, name)
            rel = os.path.relpath(path, top).replace(os.sep, "/")
            if os.path.islink(path):
                out[rel] = ["l", os.readlink(path)]
            elif os.path.isdir(path):
                out[rel] = ["d"]
            else:
                with open(path, "rb") as f:
                    out[rel] = ["f", hashlib.sha256(f.read()).hexdigest(), bool(os.lstat(path).st_mode & 0o100)]
    return out


def run(case, scratch):
    box = os.path.realpath(tempfile.mkdtemp(dir=scratch))
    with open(os.path.join(box, "outside.txt"), "w", encoding="utf-8") as f:
        f.write("keep\n")
    base = os.path.join(box, "l1", "l2", "l3")
    os.makedirs(base)
    tar = os.path.join(scratch, case["name"].replace(" ", "-") + ".tar")
    make_tar(tar, case["members"])
    error = None
    try:
        if case["via"] == "extract":
            os.mkdir(os.path.join(base, "dest"))
            watch(os.path.join(base, "dest"))
            importlib.import_module("archive.extract").safe_extract(tar, os.path.join(base, "dest"))
        elif case["via"] == "install":
            os.mkdir(os.path.join(base, "plugins"))
            watch(os.path.join(base, "plugins"))
            importlib.import_module("plugins.installer").install_bundle(tar, os.path.join(base, "plugins"))
        else:
            watch(os.path.join(base, "restored"))
            importlib.import_module("backups.restore").restore_backup(tar, os.path.join(base, "restored"))
    except Exception as e:
        error = type(e).__name__
    finally:
        outside, leaked = watch(None)
    return {"error": error, "tree": tree(box), "outside": outside, "leaked": leaked}


scratch = tempfile.mkdtemp()
out = {}
try:
    for case in payload["cases"]:
        try:
            out[case["name"]] = run(case, scratch)
        except BaseException as e:
            out[case["name"]] = {"error": f"{type(e).__name__}: {e}"[:200], "tree": None}
finally:
    shutil.rmtree(scratch, ignore_errors=True)
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
