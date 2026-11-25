#!/usr/bin/env python3
"""Entry point for the Model Router plugin's hooks and skills. See modelrouter/cli.py."""
import sys

# Too old: exit 87 before reading stdin or importing anything, so model-router.sh can try the
# next interpreter. Python compiles the whole file first, so keep all of it to syntax any
# Python can parse: no f-strings, annotations or walrus, or an old one fails with 1 instead.
if sys.version_info < (3, 9):
    sys.exit(87)


def private_cache(home, uid):
    """A bytecode folder under `home` that only this user can write, made if it is missing, else None."""
    import os
    import stat
    parent = os.path.join(home, ".cache", "model-router")
    cache = os.path.join(parent, "pycache")
    try:
        os.makedirs(cache, mode=0o700, exist_ok=True)
        for folder in (parent, cache):
            found = os.lstat(folder)
            if not stat.S_ISDIR(found.st_mode) or found.st_uid != uid:
                return None
            if found.st_mode & 0o077:
                os.chmod(folder, 0o700)
    except OSError:
        return None
    return cache


def cache_bytecode():
    """Apple's Python 3.9 writes no bytecode under -I and ships none for its library, so each hook would compile
    both again. The folder comes from the password database, since a repository's settings can set HOME for a hook."""
    if not sys.dont_write_bytecode:
        return
    try:
        import os
        import pwd
        cache = private_cache(pwd.getpwuid(os.getuid()).pw_dir, os.getuid())
    except (ImportError, KeyError, OSError):
        return
    if cache:
        sys.pycache_prefix, sys.dont_write_bytecode = cache, False


if __name__ == "__main__":
    cache_bytecode()
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from modelrouter.cli import main

    sys.exit(main())
