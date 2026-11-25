import os
import tempfile
import unittest
from pathlib import Path

from archive import listing
from archive.pack import pack
from backups import retention
from plugins import loader, manifest, registry
from plugins.installer import BundleRejected, install_bundle, uninstall


class Manifests(unittest.TestCase):
    def test_a_good_manifest_parses(self):
        found = manifest.parse(b'{"name": "hello", "version": "1.2.0", "entry": "main.py"}')
        self.assertEqual((found.name, found.version, found.entry), ("hello", "1.2.0", "main.py"))

    def test_bad_manifests_are_refused(self):
        for data in (b"{", b"[]", b'{"name": "Hello", "version": "1.2.0", "entry": "main.py"}',
                     b'{"name": "hello", "version": "1.2", "entry": "main.py"}',
                     b'{"name": "hello", "version": "1.2.0", "entry": "../main.py"}',
                     b'{"name": "hello", "version": "1.2.0", "entry": "main.py", "extra": 1}'):
            with self.subTest(data=data), self.assertRaises(manifest.ManifestError):
                manifest.parse(data)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.source = self.root / "src"
        (self.source / "assets").mkdir(parents=True)
        (self.source / "plugin.json").write_text('{"name": "hello", "version": "1.0.0", "entry": "main.py"}')
        (self.source / "main.py").write_text("def setup(app):\n    app.append('hello')\n")
        (self.source / "assets" / "logo.svg").write_text("<svg/>")
        self.bundle = self.root / "hello.tar"
        pack(self.source, self.bundle)
        self.plugins = self.root / "plugins"
        self.plugins.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_load_and_remove(self):
        install_bundle(self.bundle, self.plugins)
        app = []
        loader.load(self.plugins, "hello", "main.py", app)
        self.assertEqual(app, ["hello"])
        uninstall(self.plugins, "hello")
        self.assertEqual(registry.installed(self.plugins), {})

    def test_a_plugin_is_installed_once(self):
        install_bundle(self.bundle, self.plugins)
        with self.assertRaises(BundleRejected):
            install_bundle(self.bundle, self.plugins)

    def test_a_bundle_without_its_entry_is_refused(self):
        (self.source / "main.py").unlink()
        pack(self.source, self.bundle)
        with self.assertRaises(BundleRejected):
            install_bundle(self.bundle, self.plugins)
        self.assertEqual(os.listdir(self.plugins), [])

    def test_the_listing_reads_the_headers(self):
        entries = listing.describe(self.bundle)
        self.assertEqual([e["name"] for e in entries], ["assets", "main.py", "plugin.json", "assets/logo.svg"])
        self.assertEqual(listing.summary(entries), "3 files (103 B), 1 folder")


class Retention(unittest.TestCase):
    def test_the_newest_five_and_one_a_month_are_kept(self):
        names = [f"backup-2024-{m:02d}-{d:02d}T120000.tar" for m in (6, 5, 4, 3, 2, 1) for d in (28, 14)]
        names.sort(reverse=True)
        keep = retention.to_keep(names)
        self.assertEqual(len(keep), 5 + 3)
        self.assertIn("backup-2024-01-28T120000.tar", keep)
        self.assertNotIn("backup-2024-01-14T120000.tar", keep)


if __name__ == "__main__":
    unittest.main()
