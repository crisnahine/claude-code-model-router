"""Installing and removing plugin bundles. A bundle is a tar archive with plugin.json at its root."""
import json
import os
import shutil
import tarfile

from archive import UnsafeArchive, safe_extract

from . import manifest

MARKER = ".installed"


class BundleRejected(Exception):
    """The bundle can't be installed; nothing was installed."""


def read_manifest(bundle_path):
    try:
        with tarfile.open(bundle_path) as tar:
            try:
                member = tar.getmember("plugin.json")
            except KeyError:
                raise BundleRejected("the bundle has no plugin.json at its root") from None
            if not member.isfile():
                raise BundleRejected("plugin.json isn't a regular file")
            with tar.extractfile(member) as f:
                return manifest.parse(f.read(64 * 1024))
    except (tarfile.TarError, OSError) as e:
        raise BundleRejected(f"not a readable tar archive: {e}") from None
    except manifest.ManifestError as e:
        raise BundleRejected(str(e)) from None


def install_bundle(bundle_path, plugins_dir):
    """Install the bundle into plugins_dir/<its name> and return its Manifest. BundleRejected when the bundle is
    malformed, unsafe or already installed; plugins_dir is left as it was then."""
    found = read_manifest(bundle_path)
    dest = os.path.join(plugins_dir, found.name)
    if os.path.lexists(dest):
        raise BundleRejected(f"{found.name} is already installed")
    # Unpack beside the final folder and move it into place whole, so a half-done install never shows.
    staging = os.path.join(plugins_dir, f".{found.name}.partial")
    os.mkdir(staging)
    try:
        try:
            names = safe_extract(bundle_path, staging)
        except UnsafeArchive as e:
            raise BundleRejected(f"{found.name}: {e}") from None
        if found.entry not in names:
            raise BundleRejected(f"{found.name}: its entry {found.entry} isn't in the bundle")
        with open(os.path.join(staging, MARKER), "w", encoding="utf-8") as f:
            json.dump({"name": found.name, "version": found.version, "members": len(names)}, f)
        os.rename(staging, dest)
    finally:
        if os.path.lexists(staging):
            shutil.rmtree(staging)
    return found


def uninstall(plugins_dir, name):
    dest = os.path.join(plugins_dir, name)
    if not os.path.isfile(os.path.join(dest, MARKER)):
        raise KeyError(name)
    shutil.rmtree(dest)
