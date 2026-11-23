"""Pieces the pass checks share. A check loads this by file path and runs under `python -I`."""
import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path

# unittest is imported before the work dir joins sys.path, so a unittest.py left there can't stand in for it.
LAUNCHER = ("import sys, unittest; sys.path.append(sys.argv[1]); "
            "unittest.main(module=None, argv=['unittest', sys.argv[2]])")


def use_work(work: Path) -> None:
    # Appended, so a work-dir module can never replace a standard-library one.
    sys.path.append(str(work))


def load(work: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, Path(work) / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raises(fn, *args) -> bool:
    try:
        fn(*args)
    except ValueError:
        return True
    except Exception:
        return False
    return False


def answer(work: Path, name: str) -> dict:
    try:
        data = json.loads((Path(work) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reasons(data: dict) -> bool:
    return isinstance(data.get("reasons"), list) and bool(data["reasons"]) and all(
        isinstance(r, str) and r.strip() for r in data["reasons"])


def number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric(value):
    try:
        return float(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def finding(work: Path, target: str, lines: set) -> bool:
    """FINDING.json is one finding, exactly {"file", "line"} plus an optional "why" string, naming target at one of lines."""
    try:
        d = json.loads((Path(work) / "FINDING.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    return bool(isinstance(d, dict) and set(d) - {"why"} == {"file", "line"} and isinstance(d.get("why", ""), str)
                and isinstance(d["file"], str) and ("/" + d["file"].replace("\\", "/")).endswith("/" + target)
                and type(d["line"]) is int and d["line"] in lines)


def tests_pass(work: Path, shipped: Path, test: str) -> bool:
    if (Path(work) / test).read_bytes() != (Path(shipped) / test).read_bytes():
        return False  # the test is the spec; changing it doesn't count
    res = subprocess.run([sys.executable, "-I", "-c", LAUNCHER, str(work), test[:-3]], cwd=work,
                         capture_output=True, text=True, timeout=60)
    return res.returncode == 0 and re.search(r"Ran [1-9]\d* tests?", res.stderr) is not None and "OK" in res.stderr


def probe(work: Path, script: Path, payload, timeout: int = 45):
    """Run script in an isolated child on the work dir, with payload as JSON on stdin; return the JSON its last stdout
    line holds, or None on a timeout, a non-zero exit, no output or a last line that isn't JSON."""
    try:
        res = subprocess.run([sys.executable, "-I", str(script), str(work)], cwd=work, input=json.dumps(payload),
                             capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, UnicodeDecodeError):
        return None
    lines = res.stdout.strip().splitlines()
    if res.returncode != 0 or not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None
