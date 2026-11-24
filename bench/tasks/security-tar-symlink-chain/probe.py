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
# event: (the positions of the paths it writes, the positions of its dir_fd arguments)
WRITES = {"os.mkdir": ((0,), (2,)), "os.rmdir": ((0,), (1,)), "os.remove": ((0,), (1,)), "os.symlink": ((1,), (2,)),
          "os.link": ((1,), (2, 3)), "os.rename": ((0, 1), (2, 3)), "os.chmod": ((0,), (2,)),
          "os.chown": ((0,), (3,)), "os.utime": ((0,), (3,)), "os.truncate": ((0,), ()), "os.mkfifo": ((0,), (2,)),
          "os.mknod": ((0,), (3,)), "shutil.rmtree": ((0,), (1,))}
FOLLOWED = {"os.chmod", "os.chown", "os.utime", "os.truncate"}
OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def landing(path, follow):
    """Where a write to path really lands, following the links already on disk."""
    path = os.path.abspath(os.fsdecode(path))
    if follow:
        return os.path.realpath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def watcher():
    """An audit hook that records each write the task code makes outside the folder it was given, even one it
    removes again before it returns, and a function that sets that folder (None stops recording)."""
    state = {"top": None, "seen": []}

    def hook(event, args):
        top = state["top"]
        if top is None:
            return
        try:
            places = landings(event, args)
        except Exception:
            places = ["?"]
        for place in places:
            if not (place == top or place.startswith(top + os.sep)):
                state["seen"].append(f"{event} {os.path.relpath(place, top) if place != '?' else args!r}"[:200])

    def landings(event, args):
        if event == "open":
            path, _, flags = args
            if isinstance(path, int) or not flags & OPEN_FLAGS:
                return []
            return [landing(path, not flags & os.O_NOFOLLOW)]
        if event in WRITES:
            at, fds = WRITES[event]
            # shortcut: a write relative to a dir_fd isn't checked; no extractor here works that way.
            if any(n < len(args) and isinstance(args[n], int) and args[n] >= 0 for n in fds):
                return []
            if any(isinstance(args[n], int) for n in at):
                return []
            return [landing(args[n], event in FOLLOWED) for n in at]
        return []

    def watch(top):
        state["top"] = top and os.path.realpath(top)
        seen, state["seen"] = state["seen"], []
        return seen

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
            named = [json.loads(m[2])["name"] for m in case["members"] if m[:2] == ["file", "plugin.json"]]
            watch(os.path.join(base, "plugins", *named[:1]))
            importlib.import_module("plugins.installer").install_bundle(tar, os.path.join(base, "plugins"))
        else:
            watch(os.path.join(base, "restored"))
            importlib.import_module("backups.restore").restore_backup(tar, os.path.join(base, "restored"))
    except Exception as e:
        error = type(e).__name__
    finally:
        outside = watch(None)
    return {"error": error, "tree": tree(box), "outside": outside}


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
