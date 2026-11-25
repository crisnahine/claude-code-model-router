"""MANIFEST.json, written at the root of every backup: the sha256 of each file and the target of each link."""
import hashlib
import json
import os

from archive.pack import walk

NAME = "MANIFEST.json"


class ManifestMismatch(ValueError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def build(root):
    """{"files": {path: sha256}, "links": {path: target}} for the tree under root, paths relative and POSIX."""
    files, links = {}, {}
    for rel, path in walk(root):
        if rel == NAME:
            continue
        if os.path.islink(path):
            links[rel] = os.readlink(path)
        elif os.path.isfile(path):
            files[rel] = sha256(path)
    return {"files": files, "links": links}


def dumps(manifest):
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def verify(root, manifest):
    """ManifestMismatch unless every file and link the manifest lists is under root as it says."""
    for rel, digest in manifest.get("files", {}).items():
        path = os.path.join(root, *rel.split("/"))
        if os.path.islink(path) or not os.path.isfile(path):
            raise ManifestMismatch(f"{rel} is missing")
        if sha256(path) != digest:
            raise ManifestMismatch(f"{rel} doesn't match its checksum")
    for rel, target in manifest.get("links", {}).items():
        path = os.path.join(root, *rel.split("/"))
        if not os.path.islink(path) or os.readlink(path) != target:
            raise ManifestMismatch(f"{rel} should link to {target}")
