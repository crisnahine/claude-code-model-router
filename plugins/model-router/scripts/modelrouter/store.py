"""State files and the ledger. Writes are atomic, read-modify-write cycles take a lock,
and every read tolerates junk."""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterator

LEDGER_MAX_BYTES = 4 * 1024 * 1024
KEEP_ENTRIES = 200
KEEP_SET_ASIDE = 3  # copies of an unreadable state file kept for a person to look at
LOCK_WAIT_SECONDS = 1.0
LOCK_POLL_MAX = 0.01  # the longest sleep between tries, so a waiter isn't starved by newcomers
LOCK_STALE_SECONDS = 10.0
KEPT_RECORDS = 100  # calls kept per record file
# The version written into every state file. MIGRATIONS[n] turns a version-n file into
# version n + 1. A file with no version is version 0, which already has today's shape.
SCHEMA = 1
MIGRATIONS: dict[int, Callable[[dict], dict]] = {0: lambda data: data}


_clock: Callable[[], float] | None = None


def now() -> float:
    """The wall-clock time stamped into state and compared with it. Lock waits and stale-lock
    checks read the real clocks, so a frozen clock never stalls a lock."""
    return (_clock or time.time)()


def stamp(value: Any) -> float:
    """A stored time or count: the number itself, or 0 when it is junk, nan, infinite or too big for a float."""
    try:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return 0
    return value if ok else 0


def set_clock(clock: Callable[[], float] | None) -> None:
    """Replace the state clock, or restore the real one with None. For tests."""
    global _clock
    _clock = clock


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


# The session whose hook is running, written into the lock files it holds, so a wait can
# tell another session's lock from its own.
owner: str | None = None


class Busy(OSError):
    """Another process held the lock past LOCK_WAIT_SECONDS, so the write was skipped."""


def lock_counts() -> dict:
    """Lock waits, waits on another session's lock, skipped writes and stale locks broken, per file."""
    return _read_state(data_dir() / "locks.json")[0]


def _count(path: Path, *events: str) -> None:
    # Written without a lock, after the state lock is let go: two at once can lose a count,
    # never a state update.
    counts_path = path.with_name("locks.json")
    try:
        counts, status = _read_state(counts_path)
        if status != "ok":
            return
        entry = counts.get(path.name)
        entry = entry if isinstance(entry, dict) else {}
        for event in events:
            entry[event] = int(entry.get(event) or 0) + 1
        entry["at"] = now()
        counts[path.name] = entry
        write_atomic(counts_path, json.dumps({**counts, "schema": SCHEMA}, separators=(",", ":"), sort_keys=True))
    except (OSError, ValueError, TypeError):
        pass


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Serialize read-modify-write cycles on one file across processes. Agent calls
    issued together run their hooks at the same moment, and each would otherwise
    overwrite what the other just recorded. A lock still held after LOCK_WAIT_SECONDS
    raises Busy, so the caller skips its write: a hook must never hang a session, and
    a write without the lock could drop another hook's update."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    delay = 0.001
    handle = None
    events: list[str] = []
    holder = ""
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                # The lock file's mtime is the filesystem's real time, not the state clock.
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink()  # left behind by a process that died
                    events.append("broken")
                    continue
            except OSError:
                pass
        except PermissionError:
            # Windows reports a lock file that is mid-deletion this way, so wait for it
            # there. Anywhere else the directory isn't writable and waiting won't help.
            if os.name != "nt":
                raise
        if "waits" not in events:
            events.append("waits")
        if owner and not holder:
            try:
                holder = lock.read_text(encoding="utf-8")  # empty until the holder writes its name
            except OSError:
                pass
            if holder and holder != owner:
                events.append("other_session")
        if time.monotonic() >= deadline:
            events[events.index("waits")] = "skipped"
            _count(path, *events)
            raise Busy(f"{lock.name} is held by another process")
        time.sleep(delay)
        delay = min(delay * 2, LOCK_POLL_MAX)
    if owner:
        with contextlib.suppress(OSError):
            os.write(handle, owner.encode("utf-8"))
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
        if events:
            _count(path, *events)


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
    Returns the data and "saved", or why it wasn't: newer, unreadable, corrupt, or busy
    (the lock stayed held, so `change` edited a copy and nothing was written)."""
    try:
        with locked(path):
            data, status = _read_state(path)
            writable = status == "ok" or (status == "corrupt" and _set_aside(path))
            change(data)
            if writable:
                write_atomic(path, json.dumps({**data, "schema": SCHEMA}, separators=(",", ":"), sort_keys=True))
            return data, "saved" if writable else status
    except Busy:
        data = _read_state(path)[0]
        change(data)
        return data, "busy"


def remove_state_file(path: Path) -> str:
    """Delete one state file under its lock: removed, missing, or why it was left (newer,
    unreadable, busy). A file that fails to parse is moved aside, as a write would."""
    try:
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
    except Busy:
        return "busy"


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


def mutate_entry(name: str, key: str | None, change: Callable[[dict], Any], max_age: float | None = None) -> str:
    """Edit one record of a keyed file in place, under the file's lock. Each file keeps
    its KEEP_ENTRIES most recently written records, and with `max_age` drops those not
    written for longer than that. Returns "saved", or why nothing was written (see
    try_update; "unkeyed" with no key)."""
    if not key:
        return "unkeyed"

    def apply(data: dict) -> None:
        entry = data.get(key)
        entry = entry if isinstance(entry, dict) else {}
        change(entry)
        entry["_at"] = now()
        data[key] = entry

        def written(k: str) -> float:
            return stamp(data[k].get("_at")) if isinstance(data[k], dict) else 0
        if max_age is not None:
            for old in [k for k in data if now() - written(k) > max_age]:
                del data[old]
        if len(data) > KEEP_ENTRIES:
            for old in sorted(data, key=written)[:len(data) - KEEP_ENTRIES]:
                del data[old]

    return try_update(_keyed(name), apply)[1]


def save_entry(name: str, key: str | None, entry: dict) -> None:
    def replace(current: dict) -> None:
        current.clear()
        current.update(entry)
    mutate_entry(name, key, replace)


def update_entry(name: str, key: str | None, **changes: Any) -> None:
    mutate_entry(name, key, lambda entry: entry.update(changes))


def keep(records: dict, key: str, record: dict, kept: int | None = None) -> None:
    """Store a record stamped with the time, and drop the oldest past `kept` (KEPT_RECORDS)."""
    kept = KEPT_RECORDS if kept is None else kept
    records[key] = {**record, "at": now()}
    keys = [k for k, v in records.items() if isinstance(v, dict)]
    for old in sorted(keys, key=lambda k: stamp(records[k].get("at")))[:-kept]:
        del records[old]


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
