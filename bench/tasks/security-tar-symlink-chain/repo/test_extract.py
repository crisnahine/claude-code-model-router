import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from archive import UnsafeArchive, safe_extract
from backups.create import create_backup
from backups.restore import restore_backup
from plugins import registry
from plugins.installer import BundleRejected, install_bundle

PLUGIN = b'{"name": "hello", "version": "1.2.0", "entry": "main.py"}'


def make_tar(path, members):
    """members: ("file", name, bytes[, mode]), ("dir", name), ("link", name, target), ("hard", name, target) or
    ("fifo", name), written in the order given."""
    with tarfile.open(path, "w") as tar:
        for kind, name, *rest in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size, info.mode = len(rest[0]), rest[1] if len(rest) > 1 else 0o644
                tar.addfile(info, io.BytesIO(rest[0]))
                continue
            info.type, info.mode = {"dir": (tarfile.DIRTYPE, 0o755), "link": (tarfile.SYMTYPE, 0o777),
                                    "hard": (tarfile.LNKTYPE, 0o644), "fifo": (tarfile.FIFOTYPE, 0o644)}[kind]
            info.linkname = rest[0] if rest else ""
            tar.addfile(info)
    return path


def tree(top):
    """{relative path: ("dir",) | ("file", bytes, executable) | ("link", target)} for everything under top."""
    out = {}
    for here, dirs, files in os.walk(top):
        for name in dirs + files:
            path = os.path.join(here, name)
            rel = os.path.relpath(path, top).replace(os.sep, "/")
            if os.path.islink(path):
                out[rel] = ("link", os.readlink(path))
            elif os.path.isdir(path):
                out[rel] = ("dir",)
            else:
                out[rel] = ("file", Path(path).read_bytes(), bool(os.stat(path).st_mode & 0o100))
    return out


class Extract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = Path(os.path.realpath(self.tmp.name)) / "box"
        self.dest = self.box / "dest"
        self.dest.mkdir(parents=True)
        (self.box / "keep.txt").write_text("not yours\n")

    def tearDown(self):
        self.tmp.cleanup()

    def assertUntouched(self):
        self.assertEqual(tree(self.box), {"dest": ("dir",), "keep.txt": ("file", b"not yours\n", False)})

    def workspace(self):
        """A deploy folder as users back it up: releases, a current link, links into it and a forward link."""
        ws = Path(self.tmp.name) / "workspace"
        for rel, data in {"releases/v1/app.conf": b"port=8000\n", "releases/v2/app.conf": b"port=8080\n",
                          "releases/v2/bin/run": b"#!/bin/sh\nexec app\n", "shared/data.db": b"\x00db",
                          "logs/2024-05.log": b"may\n", "logs/2024-06.log": b"june\n", "notes/café.txt": b"ok\n"}.items():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_bytes(data)
        os.chmod(ws / "releases/v2/bin/run", 0o755)
        (ws / "empty").mkdir()
        os.symlink("releases/v2", ws / "current")
        os.symlink("current/app.conf", ws / "config")
        os.symlink("logs/2024-06.log", ws / "latest.log")
        os.symlink("../../shared", ws / "releases/v2/shared")
        return ws

    def test_a_backup_restores_as_it_was(self):
        ws = self.workspace()
        backup = Path(self.tmp.name) / "backup.tar"
        create_backup(ws, backup)
        target = self.box / "restored"
        restore_backup(backup, target)
        got = tree(target)
        self.assertEqual({k: v for k, v in got.items() if k not in ("MANIFEST.json", ".restored")}, tree(ws))
        self.assertEqual((target / "config").read_bytes(), b"port=8080\n")
        self.assertEqual((target / "releases/v2/shared/data.db").read_bytes(), b"\x00db")

    def test_a_plugin_bundle_installs(self):
        bundle = make_tar(Path(self.tmp.name) / "hello.tar", [
            ("file", "plugin.json", PLUGIN), ("file", "main.py", b"print('hi')\n"), ("dir", "assets"),
            ("file", "assets/logo.svg", b"<svg/>"), ("link", "logo.svg", "assets/logo.svg"),
            ("file", "bin/hello", b"#!/bin/sh\n", 0o755)])
        plugins = self.box / "plugins"
        plugins.mkdir()
        install_bundle(bundle, plugins)
        self.assertEqual(registry.installed(plugins), {"hello": "1.2.0"})
        self.assertEqual((plugins / "hello/logo.svg").read_bytes(), b"<svg/>")
        self.assertTrue(os.stat(plugins / "hello/bin/hello").st_mode & 0o100)

    def test_members_that_reach_outside_are_refused_before_anything_is_written(self):
        good = ("file", "readme.txt", b"hi\n")
        for bad in [("file", "../evil.txt", b"x"), ("file", "/tmp/evil.txt", b"x"),
                    ("file", "a/../../evil.txt", b"x"), ("link", "out", "../.."), ("link", "abs", "/etc/passwd"),
                    ("link", "deep/out", "../../keep.txt"), ("hard", "h", "readme.txt"), ("fifo", "pipe")]:
            with self.subTest(member=bad):
                tar = make_tar(Path(self.tmp.name) / "bad.tar", [good, ("dir", "deep"), bad])
                with self.assertRaises(UnsafeArchive):
                    safe_extract(tar, self.dest)
                self.assertUntouched()

    def test_a_name_listed_twice_is_refused(self):
        tar = make_tar(Path(self.tmp.name) / "twice.tar", [("file", "a.txt", b"1"), ("file", "./a.txt", b"2")])
        with self.assertRaises(UnsafeArchive):
            safe_extract(tar, self.dest)
        self.assertUntouched()

    def test_a_refused_bundle_installs_nothing(self):
        bundle = make_tar(Path(self.tmp.name) / "bad.tar", [("file", "plugin.json", PLUGIN),
                                                             ("file", "main.py", b""), ("link", "up", "../..")])
        plugins = self.box / "plugins"
        plugins.mkdir()
        with self.assertRaises(BundleRejected):
            install_bundle(bundle, plugins)
        self.assertEqual(os.listdir(plugins), [])

    def test_a_refused_backup_leaves_no_folder(self):
        tar = make_tar(Path(self.tmp.name) / "bad.tar", [("file", "MANIFEST.json", b"{}"), ("file", "../x", b"")])
        with self.assertRaises(UnsafeArchive):
            restore_backup(tar, self.box / "restored")
        self.assertFalse((self.box / "restored").exists())


if __name__ == "__main__":
    unittest.main()
