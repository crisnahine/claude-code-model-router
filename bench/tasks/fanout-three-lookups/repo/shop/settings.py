"""Settings: config/base.ini, then config/<SHOP_ENV>.ini over it, then SHOP_<SECTION>_<KEY> environment variables."""
import configparser
import os
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"
_parser = None


def _load():
    global _parser
    if _parser is None:
        parser = configparser.ConfigParser()
        files = [CONFIG / "base.ini"]
        if os.environ.get("SHOP_ENV"):
            files.append(CONFIG / f"{os.environ['SHOP_ENV']}.ini")
        parser.read(files)
        _parser = parser
    return _parser


def get(section, key, fallback=None):
    override = os.environ.get(f"SHOP_{section}_{key}".upper())
    if override is not None:
        return override
    return _load().get(section, key, fallback=fallback)


def get_int(section, key, fallback=None):
    value = get(section, key)
    return fallback if value is None else int(value)


def get_float(section, key, fallback=None):
    value = get(section, key)
    return fallback if value is None else float(value)


def get_bool(section, key, fallback=False):
    value = get(section, key)
    return fallback if value is None else value.strip().lower() in ("1", "true", "yes", "on")
