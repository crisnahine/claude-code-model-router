"""plugin.json, the file at the root of every bundle that says what the plugin is."""
import json
import re
from dataclasses import dataclass

NAME = re.compile(r"[a-z][a-z0-9-]{1,40}")
VERSION = re.compile(r"\d+\.\d+\.\d+")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    entry: str
    description: str = ""


def parse(data):
    """A Manifest from plugin.json's bytes, or ManifestError."""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ManifestError(f"plugin.json isn't JSON: {e}") from None
    if not isinstance(raw, dict):
        raise ManifestError("plugin.json must hold an object")
    unknown = sorted(set(raw) - {"name", "version", "entry", "description"})
    if unknown:
        raise ManifestError(f"plugin.json has unknown keys: {', '.join(unknown)}")
    name, version, entry = raw.get("name"), raw.get("version"), raw.get("entry")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ManifestError(f"bad plugin name {name!r}: lowercase letters, digits and -, starting with a letter")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ManifestError(f"bad version {version!r}: use MAJOR.MINOR.PATCH")
    if (not isinstance(entry, str) or not entry.endswith(".py") or entry.startswith("/")
            or ".." in entry.split("/")):
        raise ManifestError(f"bad entry {entry!r}: a .py file in the bundle")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ManifestError("description must be a string")
    return Manifest(name, version, entry, description)
