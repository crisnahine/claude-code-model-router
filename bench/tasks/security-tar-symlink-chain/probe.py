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

passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)
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
            importlib.import_module("archive.extract").safe_extract(tar, os.path.join(base, "dest"))
        elif case["via"] == "install":
            os.mkdir(os.path.join(base, "plugins"))
            importlib.import_module("plugins.installer").install_bundle(tar, os.path.join(base, "plugins"))
        else:
            importlib.import_module("backups.restore").restore_backup(tar, os.path.join(base, "restored"))
    except Exception as e:
        error = type(e).__name__
    return {"error": error, "tree": tree(box)}


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
