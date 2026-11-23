import re
import subprocess


def changelog(ref):
    if not re.fullmatch(r"[0-9a-f]{7,40}", ref):
        raise ValueError(ref)
    return subprocess.run(["git", "show", f"{ref}:CHANGELOG.md"], capture_output=True, text=True, check=True).stdout
