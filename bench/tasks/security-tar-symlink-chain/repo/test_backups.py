import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from backups import manifest
from backups.create import create_backup
from backups.restore import restore_backup

HERE = Path(__file__).resolve().parent


class Backups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.ws = self.root / "ws"
        (self.ws / "docs").mkdir(parents=True)
        (self.ws / "docs" / "plan.md").write_text("# plan\n")
        (self.ws / "todo.txt").write_text("ship it\n")
        os.symlink("docs/plan.md", self.ws / "plan")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_manifest_lists_files_and_links(self):
        built = manifest.build(self.ws)
        self.assertEqual(sorted(built["files"]), ["docs/plan.md", "todo.txt"])
        self.assertEqual(built["links"], {"plan": "docs/plan.md"})

    def test_the_same_workspace_packs_to_the_same_bytes(self):
        create_backup(self.ws, self.root / "a.tar")
        create_backup(self.ws, self.root / "b.tar")
        self.assertEqual((self.root / "a.tar").read_bytes(), (self.root / "b.tar").read_bytes())

    def test_a_tampered_backup_is_refused_and_leaves_no_folder(self):
        create_backup(self.ws, self.root / "good.tar")
        with tarfile.open(self.root / "good.tar") as src, tarfile.open(self.root / "bad.tar", "w") as dst:
            for member in src.getmembers():
                data = src.extractfile(member) if member.isfile() else None
                if member.name == "todo.txt":
                    member.size = 4
                    data = io.BytesIO(b"nope")
                dst.addfile(member, data)
        with self.assertRaises(manifest.ManifestMismatch):
            restore_backup(self.root / "bad.tar", self.root / "restored")
        self.assertFalse((self.root / "restored").exists())

    def test_a_backup_without_a_manifest_is_refused(self):
        with tarfile.open(self.root / "bare.tar", "w") as tar:
            tar.add(self.ws / "todo.txt", arcname="todo.txt")
        with self.assertRaises(manifest.ManifestMismatch):
            restore_backup(self.root / "bare.tar", self.root / "restored")

    def test_restoring_over_an_existing_folder_is_refused(self):
        create_backup(self.ws, self.root / "good.tar")
        (self.root / "restored").mkdir()
        with self.assertRaises(FileExistsError):
            restore_backup(self.root / "good.tar", self.root / "restored")

    def test_the_command_line_round_trip(self):
        tar, target = self.root / "cli.tar", self.root / "cli"
        for args in (["create", str(self.ws), str(tar)], ["restore", str(tar), str(target)]):
            res = subprocess.run([sys.executable, "-m", "backups", *args], cwd=HERE, capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(os.readlink(target / "plan"), "docs/plan.md")
        self.assertEqual(json.loads((target / "MANIFEST.json").read_text())["links"], {"plan": "docs/plan.md"})


if __name__ == "__main__":
    unittest.main()
