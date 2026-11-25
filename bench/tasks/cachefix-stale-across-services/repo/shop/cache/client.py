"""The read-through cache the services share."""
from .backend import MemoryBackend
from .tags import TagIndex


class Cache:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else MemoryBackend()
        self.index = TagIndex()
        self.invalidations = 0
        self.dropped = 0

    def get_or_load(self, key, loader, tags=()):
        """The value cached under key, or else loader()'s result, cached under key with tags. An error from loader
        caches nothing."""
        found, value = self.backend.get(key)
        if found:
            return value
        value = loader()
        self.backend.set(key, value)
        self.index.add(key, tags)
        return value

    def peek(self, key):
        """The cached value, or None, without loading anything."""
        found, value = self.backend.get(key)
        return value if found else None

    def invalidate(self, tags):
        """Drop every value carrying any of tags; return the keys dropped."""
        tags = sorted(set(tags))
        if not tags:
            return []
        keys = self.index.keys_for(tags)
        self.dropped += self.backend.delete_many(keys)
        self.index.forget(keys)
        self.invalidations += 1
        return keys

    def clear(self):
        self.backend.clear()
        self.index.clear()

    def stats(self):
        return {"entries": len(self.backend), "hits": self.backend.hits, "misses": self.backend.misses,
                "invalidations": self.invalidations, "dropped": self.dropped}
