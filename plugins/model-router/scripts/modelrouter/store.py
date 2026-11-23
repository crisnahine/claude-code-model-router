"""State files and the ledger. Writes are atomic, read-modify-write cycles take a lock,
and every read tolerates junk."""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterator

LEDGER_MAX_BYTES = 4 * 1024 * 1024
KEEP_ENTRIES = 200
KEEP_SET_ASIDE = 3  # copies of an unreadable state file kept for a person to look at
LOCK_WAIT_SECONDS = 2.0
LOCK_STALE_SECONDS = 10.0
# The version written into every state file. MIGRATIONS[n] turns a version-n file into
# version n + 1. A file with no version is version 0, which already has today's shape.
SCHEMA = 1
MIGRATIONS: dict[int, Callable[[dict], dict]] = {0: lambda data: data}


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def data_dir() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(env) if env else claude_home() / "plugins" / "data" / "model-router-local"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_json(path: Path, value: Any) -> None:
    write_atomic(path, json.dumps(value, indent=2, sort_keys=True))


def write_atomic(path: Path, text: str) -> None:
    # A unique temp name per process, then an atomic rename: a reader never sees a
    # half-written file.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(40):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # Windows: another process has the file open this instant
            time.sleep(0.025)
    try:
        tmp.unlink()
    except OSError:
        pass
    raise OSError(f"could not replace {path.name}")


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Serialize read-modify-write cycles on one file across processes. Agent calls
    issued together run their hooks at the same moment, and each would otherwise
    overwrite what the other just recorded. If the lock can't be had in two seconds
    the work goes ahead without it: a hook must never hang a session."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    handle = None
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink()  # left behind by a process that died
                    continue
            except OSError:
                pass
        except PermissionError:
            # Windows reports a lock file that is mid-deletion this way, so wait for it
            # there. Anywhere else the directory isn't writable and waiting won't help.
            if os.name != "nt":
                break
        except OSError:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    try:
        yield
    finally:
        if handle is not None:
            os.close(handle)
            for _ in range(20):
                try:
                    lock.unlink()
                    break
                except PermissionError:  # Windows: something has the lock file open
                    time.sleep(0.01)
                except OSError:
                    break


def _read_state(path: Path) -> tuple[dict, str]:
    """One state file, migrated to SCHEMA, without its version key, and how it read:
    ok, corrupt, newer (written by a later release) or unreadable."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, "ok"
    except OSError:
        return {}, "unreadable"
    try:
        data = json.loads(raw)
    except ValueError:
        return {}, "corrupt"
    if not isinstance(data, dict):
        return {}, "corrupt"
    version = data.pop("schema", 0)
    if type(version) is not int or version < 0:
        return {}, "corrupt"
    if version > SCHEMA:
        return data, "newer"
    while version < SCHEMA:
        data = MIGRATIONS[version](data)
        version += 1
    return data, "ok"


def load_state_file(path: Path) -> dict:
    return _read_state(path)[0]


def _set_aside(path: Path) -> bool:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"{path.name}.corrupt-{stamp}"
    # Two set-asides can share a stamp (Windows' clock ticks in about 15 ms), so a counter one
    # above the newest copy with this stamp keeps each one and still sorts it last. Never overwrite.
    taken = sorted(p.name for p in path.parent.glob(base + "*"))
    n = 0 if not taken else 1 if taken[-1] == base else int(taken[-1][len(base) + 1:]) + 1
    if n > 999:
        return False
    target = path.with_name(base + (f"-{n:03d}" if n else ""))
    try:
        os.replace(path, target)
    except OSError:
        return False
    # Stamps sort by time, so the oldest copies come first.
    for old in sorted(path.parent.glob(f"{path.name}.corrupt-*"))[:-KEEP_SET_ASIDE]:
        try:
            old.unlink()
        except OSError:
            pass
    return True


def set_aside() -> list[Path]:
    """State files that failed to parse and were moved out of the way."""
    return sorted(data_dir().glob("*.corrupt-*"))


def newer_files() -> list[Path]:
    """State files a later release wrote, which this one reads but never updates."""
    return [p for p in sorted(data_dir().glob("*.json")) if _read_state(p)[1] == "newer"]


def try_update(path: Path, change: Callable[[dict], Any]) -> tuple[dict, str]:
    """Load one state file, let `change` edit it in place, and save it, under its lock.
    A file that fails to parse is moved aside first; one from a newer release, or one
    that can't be read or moved, is never written. Saved compact, with its version.
    Returns the data and "saved", or why it wasn't: newer, unreadable or corrupt."""
    with locked(path):
        data, status = _read_state(path)
        writable = status == "ok" or (status == "corrupt" and _set_aside(path))
        change(data)
        if writable:
            write_atomic(path, json.dumps({**data, "schema": SCHEMA}, separators=(",", ":"), sort_keys=True))
        return data, "saved" if writable else status


def remove_state_file(path: Path) -> str:
    """Delete one state file under its lock: removed, missing, or why it was left (newer,
    unreadable). A file that fails to parse is moved aside, as a write would."""
    with locked(path):
        status = _read_state(path)[1]
        if status in ("newer", "unreadable"):
            return status
        if status == "corrupt":
            return "removed" if _set_aside(path) else "unreadable"
        try:
            path.unlink()
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "unreadable"
        return "removed"


def update_dict(path: Path, change: Callable[[dict], Any]) -> dict:
    return try_update(path, change)[0]


def state_path() -> Path:
    return data_dir() / "state.json"


def load_state() -> dict:
    return load_state_file(state_path())


def update_state(change: Callable[[dict], Any]) -> dict:
    return update_dict(state_path(), change)


def _keyed(name: str) -> Path:
    return data_dir() / f"{name}.json"


def load_entry(name: str, key: str | None) -> dict:
    entry = load_state_file(_keyed(name)).get(key) if key else None
    return entry if isinstance(entry, dict) else {}


def mutate_entry(name: str, key: str | None, change: Callable[[dict], Any]) -> None:
    """Edit one record of a keyed file in place, under the file's lock. Each file keeps
    its KEEP_ENTRIES most recently written records."""
    if not key:
        return

    def apply(data: dict) -> None:
        entry = data.get(key)
        entry = entry if isinstance(entry, dict) else {}
        change(entry)
        entry["_at"] = time.time()
        data[key] = entry
        if len(data) > KEEP_ENTRIES:
            def written(k: str) -> float:
                return data[k].get("_at", 0) if isinstance(data[k], dict) else 0
            for old in sorted(data, key=written)[:len(data) - KEEP_ENTRIES]:
                del data[old]

    update_dict(_keyed(name), apply)


def save_entry(name: str, key: str | None, entry: dict) -> None:
    def replace(current: dict) -> None:
        current.clear()
        current.update(entry)
    mutate_entry(name, key, replace)


def update_entry(name: str, key: str | None, **changes: Any) -> None:
    mutate_entry(name, key, lambda entry: entry.update(changes))


def ledger_path() -> Path:
    return data_dir() / "ledger.jsonl"


def append_ledger(entry: dict) -> None:
    entry = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), **entry}
    path = ledger_path()
    try:
        # The append takes the lock too: a row written between a trim's read and its
        # replace would land in the file the trim throws away.
        with locked(path):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            if path.stat().st_size > LEDGER_MAX_BYTES:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                write_atomic(path, "".join(lines[len(lines) // 2:]))
    except OSError:
        pass


def read_ledger(days: int | None = None) -> list[dict]:
    rows: list[dict] = []
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days) if days else None
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
            if cutoff and _dt.datetime.fromisoformat(row["ts"]) < cutoff:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
