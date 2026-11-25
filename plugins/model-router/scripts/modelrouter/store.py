"""State files and the ledger. Writes are atomic, read-modify-write cycles take a lock,
and every read tolerates junk."""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import math
import os
import stat
import time
from pathlib import Path
from typing import Any, Callable, Iterator

LEDGER_MAX_BYTES = 4 * 1024 * 1024
LEDGER_MAX_DAYS = 30  # Claude Code's own default cleanupPeriodDays, which never reaches plugin data
KEEP_ENTRIES = 200
KEEP_SET_ASIDE = 3  # copies of an unreadable state file kept for a person to look at
LOCK_WAIT_SECONDS = 1.0
LOCK_POLL_MAX = 0.01  # the longest sleep between tries, so a waiter isn't starved by newcomers
KEPT_RECORDS = 100  # calls kept per record file
FILE_MAX_BYTES = 4 * 1024 * 1024  # an override file or plugin record over this says nothing
# The version written into each shared file (state.json, locks.json), which
# several releases can write at once. A file with no version has today's shape.
SCHEMA = 1
DURABLE = ("state.json", "locks.json")


_clock: Callable[[], float] | None = None


def now() -> float:
    """The wall-clock time stamped into state and compared with it. Lock waits read the
    real clock, so a frozen clock never stalls a lock."""
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


_private: set[Path] = set()


def data_dir() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(env) if env else claude_home() / "plugins" / "data" / "model-router-local"
    path.mkdir(parents=True, exist_ok=True)
    if path not in _private:
        # Claude Code creates the folder itself, so a mode on mkdir would not apply.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
        _private.add(path)
    return path


def loads(raw: str | bytes, **options: Any) -> Any:
    """json.loads, with the RecursionError that deep nesting raises turned into the ValueError every caller handles."""
    try:
        return json.loads(raw, **options)
    except RecursionError as exc:
        raise ValueError("nested too deeply") from exc


def read_head(path: Path, size: int) -> bytes | None:
    """At most `size` bytes from the start of a regular file, else None. The open doesn't block, so a FIFO a
    repository's symlink points at can't hang a hook."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except (OSError, ValueError):
        return None
    try:
        with open(fd, "rb") as fh:
            return fh.read(size) if stat.S_ISREG(os.fstat(fd).st_mode) else None
    except OSError:
        return None


def _short_int(digits: str) -> float:
    # Python turns digits into an int in quadratic time, so a megabyte of them would stall the hook. Longer reads as
    # infinite, which every reader already drops as not a finite number.
    return int(digits) if len(digits) <= 64 else math.inf


def load_dict(path: Path) -> dict:
    """A JSON object file, or {} when it is missing, unreadable, malformed, nested too deeply or over FILE_MAX_BYTES."""
    raw = read_head(path, FILE_MAX_BYTES + 1)
    if raw is None or len(raw) > FILE_MAX_BYTES:
        return {}
    try:
        value = loads(raw, parse_int=_short_int)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def write_atomic(path: Path, text: str) -> None:
    # A unique temp name per process, then an atomic rename: a reader never sees a
    # half-written file.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with _private_file(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL) as fh:
        fh.write(text)
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


def _private_file(path: Path, flags: int) -> Any:
    # Without O_BINARY a Windows descriptor translates newlines a second time, so each row would end in \r\r\n.
    fd = os.open(str(path), flags | getattr(os, "O_BINARY", 0), 0o600)
    return os.fdopen(fd, "a" if flags & os.O_APPEND else "w", encoding="utf-8")


# The session whose hook is running, written into the lock files it holds, so a wait can
# tell another session's lock from its own.
owner: str | None = None


class Busy(OSError):
    """Another process held the lock past LOCK_WAIT_SECONDS, so the write was skipped."""


def lock_counts() -> dict:
    """Lock waits, waits on another session's lock and skipped writes, per file."""
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
    a write without the lock could drop another hook's update. The lock is the OS's, on a
    file that stays on disk, so a process that dies lets go of it at once. On a filesystem
    that can't lock at all, the write goes ahead without one."""
    lock = path.with_name(path.name + ".flock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    delay = 0.001
    events: list[str] = []
    holder = ""
    handle = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                _try_lock(handle)
                break
            except _HELD:
                pass
            except OSError:
                break  # this filesystem can't lock: a write without one beats no routing
            if "waits" not in events:
                events.append("waits")
            if owner and not holder:
                try:
                    # Byte 0 is the locked byte, which Windows won't let a waiter read.
                    with open(lock, "rb") as fh:
                        fh.seek(1)
                        holder = fh.read().decode("utf-8", "replace")  # empty until the holder writes its name
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
        with contextlib.suppress(OSError):
            os.ftruncate(handle, 0)  # a holder that was killed left its name
            if owner:
                os.write(handle, b" " + owner.encode("utf-8"))
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                os.ftruncate(handle, 0)
            with contextlib.suppress(OSError):
                _unlock(handle)
            if events:
                _count(path, *events)
    finally:
        os.close(handle)


if os.name == "nt":
    import msvcrt

    # The CRT's _locking sets EACCES (PermissionError) for a held range; a filesystem that can't lock fails otherwise.
    _HELD = PermissionError

    def _try_lock(handle: int) -> None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)

    def _unlock(handle: int) -> None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    # Anything else, such as ENOLCK on a mount without flock, means this filesystem can't lock, so `locked` writes without one.
    _HELD = BlockingIOError

    def _try_lock(handle: int) -> None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: int) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)


def _read_state(path: Path, durable: bool = True) -> tuple[dict, str]:
    """One state file without its version key, and how it read: ok, corrupt, newer (a shared
    file written by a later release) or unreadable. A per-session file has no version to check."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, "ok"
    except OSError:
        return {}, "unreadable"
    try:
        data = loads(raw)
    except ValueError:
        return {}, "corrupt"
    if not isinstance(data, dict):
        return {}, "corrupt"
    version = data.pop("schema", 0)
    if not durable:
        return data, "ok"
    if type(version) is not int or version < 0:
        return {}, "corrupt"
    return data, "newer" if version > SCHEMA else "ok"


def load_state_file(path: Path, durable: bool = True) -> dict:
    return _read_state(path, durable)[0]


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
    """Shared files a later release wrote, which this one reads but never updates."""
    return [p for p in (data_dir() / name for name in DURABLE) if _read_state(p)[1] == "newer"]


def try_update(path: Path, change: Callable[[dict], Any], durable: bool = True) -> tuple[dict, str]:
    """Load one state file, let `change` edit it in place, and save it, under its lock, compact.
    A shared (`durable`) file is saved with its version; one that fails to parse is moved aside
    first, and one from a newer release, or one that can't be read or moved, is never written.
    A per-session file that fails to parse is replaced. Returns the data and "saved", or why it
    wasn't: newer, unreadable, corrupt, or busy (the lock stayed held, so `change` edited a copy
    and nothing was written)."""
    try:
        with locked(path):
            data, status = _read_state(path, durable)
            writable = status == "ok" or (status == "corrupt" and (not durable or _set_aside(path)))
            change(data)
            if writable:
                saved = {**data, "schema": SCHEMA} if durable else data
                write_atomic(path, json.dumps(saved, separators=(",", ":"), sort_keys=True))
            return data, "saved" if writable else status
    except Busy:
        data = _read_state(path, durable)[0]
        change(data)
        return data, "busy"


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
    entry = load_state_file(_keyed(name), durable=False).get(key) if key else None
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

    return try_update(_keyed(name), apply, durable=False)[1]


def save_entry(name: str, key: str | None, entry: dict) -> None:
    def replace(current: dict) -> None:
        current.clear()
        current.update(entry)
    mutate_entry(name, key, replace)


def update_entry(name: str, key: str | None, **changes: Any) -> None:
    mutate_entry(name, key, lambda entry: entry.update(changes))


def drop_entry(name: str, key: str | None) -> None:
    if key:
        try_update(_keyed(name), lambda data: data.pop(key, None), durable=False)


def keep(records: dict, key: str, record: dict, kept: int | None = None) -> None:
    """Store a record stamped with the time, and drop the oldest past `kept` (KEPT_RECORDS)."""
    kept = KEPT_RECORDS if kept is None else kept
    records[key] = {**record, "at": now()}
    keys = [k for k, v in records.items() if isinstance(v, dict)]
    for old in sorted(keys, key=lambda k: stamp(records[k].get("at")))[:-kept]:
        del records[old]


def ledger_path() -> Path:
    return data_dir() / "ledger.jsonl"


def _older(line: str, cutoff: _dt.datetime) -> bool | None:
    """Whether a ledger line's row is older than `cutoff`, or None when it has no readable time.
    A time with no zone is read as UTC."""
    try:
        ts = _dt.datetime.fromisoformat(loads(line)["ts"])
        return (ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)) < cutoff
    except (KeyError, ValueError, TypeError):
        return None


def append_ledger(entry: dict) -> None:
    """Append one row. Rows older than LEDGER_MAX_DAYS go once the oldest one is, and past
    LEDGER_MAX_BYTES the older half goes too."""
    now = _dt.datetime.now(_dt.timezone.utc)
    entry = {"ts": now.isoformat(timespec="seconds"), **entry}
    cutoff = now - _dt.timedelta(days=LEDGER_MAX_DAYS)
    path = ledger_path()
    try:
        # The append takes the lock too: a row written between a trim's read and its
        # replace would land in the file the trim throws away.
        with locked(path):
            with _private_file(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND) as fh:
                fh.write(json.dumps(entry) + "\n")
            # A bad byte reads as U+FFFD, so its line is one more that doesn't parse.
            with path.open(encoding="utf-8", errors="replace") as fh:
                oldest = next((found for found in map(lambda line: _older(line, cutoff), fh) if found is not None), False)
            big = path.stat().st_size > LEDGER_MAX_BYTES
            if oldest or big:
                lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                         if line.strip() and not _older(line, cutoff)]
                if sum(len(line.encode("utf-8")) for line in lines) > LEDGER_MAX_BYTES:
                    lines = lines[len(lines) // 2:]
                write_atomic(path, "".join(lines))
    except OSError:
        pass
