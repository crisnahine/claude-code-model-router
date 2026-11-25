"""Loading an installed plugin: import its entry file under a name of its own and call its setup()."""
import importlib.util

from . import registry

PREFIX = "installed_plugin_"


class LoadError(Exception):
    pass


def load(plugins_dir, name, entry, app):
    """Import plugins_dir/name/entry and call its setup(app); return the module."""
    path = registry.entry_point(plugins_dir, name, entry)
    spec = importlib.util.spec_from_file_location(PREFIX + name.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise LoadError(f"{name}: can't import {entry}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise LoadError(f"{name}: {entry} failed to import: {e}") from e
    setup = getattr(module, "setup", None)
    if not callable(setup):
        raise LoadError(f"{name}: {entry} has no setup(app)")
    setup(app)
    return module
