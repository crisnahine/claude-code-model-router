"""The store behind the cache. Production runs the same interface against a shared cache server; tests and the dev
server use this in-memory one."""
import copy

_MISSING = object()


class MemoryBackend:
    def __init__(self):
        self._data = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        """(True, value) for a stored key, (False, None) otherwise."""
        value = self._data.get(key, _MISSING)
        if value is _MISSING:
            self.misses += 1
            return False, None
        self.hits += 1
        return True, copy.deepcopy(value)

    def set(self, key, value):
        # Copied in and out, so no caller can change what another one reads.
        self._data[key] = copy.deepcopy(value)

    def delete(self, key):
        return self._data.pop(key, _MISSING) is not _MISSING

    def delete_many(self, keys):
        return sum(1 for key in keys if self.delete(key))

    def keys(self, prefix=""):
        return sorted(k for k in self._data if k.startswith(prefix))

    def clear(self):
        self._data.clear()

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data
