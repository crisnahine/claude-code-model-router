"""Which plugins are installed: every folder in the plugins folder that holds an install marker."""
import json
import os

from .installer import MARKER


def installed(plugins_dir):
    """{name: version} for each installed plugin."""
    out = {}
    if not os.path.isdir(plugins_dir):
        return out
    for name in sorted(os.listdir(plugins_dir)):
        marker = os.path.join(plugins_dir, name, MARKER)
        if os.path.isfile(marker):
            with open(marker, encoding="utf-8") as f:
                out[name] = json.load(f)["version"]
    return out


def entry_point(plugins_dir, name, entry):
    """The path of a plugin's entry file, which the app imports to load the plugin."""
    path = os.path.join(plugins_dir, name, entry)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return path
