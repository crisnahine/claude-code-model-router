"""State-file lock regressions."""
from __future__ import annotations

import errno
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_model_router import SCRIPTS, Sandbox, store


class StoreLock(Sandbox):
    def test_two_waiters_on_a_lock_left_by_a_dead_hook_never_both_hold_it(self):
        self.data.mkdir(exist_ok=True)
        path = self.data / "sample.json"
        store.save_json(path, {"n": 0})
        lock = self.data / "sample.json.flock"
        lock.write_text("dead")
        os.utime(lock, (time.time() - 60, time.time() - 60))
        real_unlink = Path.unlink
        a_inside = threading.Event()
        parked = [False]

        def unlink(self_path, *args, **kwargs):
            # B is descheduled after it decides the lock is dead and before it removes it.
            if self_path == lock and threading.current_thread().name == "B" and not parked[0]:
                parked[0] = True
                a_inside.wait(5)
            return real_unlink(self_path, *args, **kwargs)

        guard = threading.Lock()
        inside = [0]
        most = [0]

        def add(name):
            with store.locked(path):
                with guard:
                    inside[0] += 1
                    most[0] = max(most[0], inside[0])
                n = store.load_state_file(path).get("n", 0)
                if name == "A":
                    a_inside.set()
                    time.sleep(0.3)
                store.save_json(path, {"n": n + 1})
                with guard:
                    inside[0] -= 1

        Path.unlink = unlink
        try:
            b = threading.Thread(target=add, args=("B",), name="B")
            b.start()
            time.sleep(0.05)
            a = threading.Thread(target=add, args=("A",), name="A")
            a.start()
            a.join()
            b.join()
        finally:
            Path.unlink = real_unlink
        self.assertEqual(most[0], 1)
        self.assertEqual(store.load_state_file(path).get("n"), 2)

    def test_a_hook_killed_while_holding_a_lock_does_not_skip_the_next_write(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1])\n"
                "from modelrouter import store\n"
                "with store.locked(store.data_dir() / 'state.json'):\n"
                "    print('ready', flush=True)\n"
                "    time.sleep(60)\n")
        holder = subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS)], stdout=subprocess.PIPE,
                                  text=True, env={**os.environ})
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        holder.kill()
        holder.wait(timeout=30)
        holder.stdout.close()
        self.assertEqual(store.try_update(self.data / "state.json", lambda data: data.update(a=1))[1], "saved")
        self.assertEqual(store.load_state_file(self.data / "state.json"), {"a": 1})

    def test_a_lock_let_go_names_no_holder(self):
        self.data.mkdir(exist_ok=True)
        path = self.data / "state.json"
        store.owner = "s1"
        with store.locked(path):
            with open(self.data / "state.json.flock", "rb") as held:
                held.seek(1)  # byte 0 is the locked byte, which Windows lets nobody else read
                self.assertIn(b"s1", held.read())
        self.assertEqual((self.data / "state.json.flock").read_bytes(), b"")

    @unittest.skipIf(os.name == "nt", "flock is POSIX")
    def test_a_filesystem_without_locks_writes_without_one(self):
        # Some NFS, SMB and FUSE mounts can't lock. A lost update there beats losing routing or a second per write.
        self.data.mkdir(exist_ok=True)
        path = self.data / "state.json"
        for code in (errno.ENOLCK, errno.EOPNOTSUPP):
            started = time.monotonic()
            with mock.patch.object(store.fcntl, "flock", side_effect=OSError(code, os.strerror(code))):
                self.assertEqual(store.try_update(path, lambda data: data.update({str(code): 1}))[1], "saved")
            self.assertLess(time.monotonic() - started, store.LOCK_WAIT_SECONDS / 2)
        self.assertEqual({k: v for k, v in store.load_state_file(path).items() if k.isdigit()},
                         {str(errno.ENOLCK): 1, str(errno.EOPNOTSUPP): 1})
        self.assertEqual(store.lock_counts(), {})


if __name__ == "__main__":
    unittest.main()
