"""Which cached keys carry which tags."""


class TagIndex:
    def __init__(self):
        self._keys = {}  # tag -> keys carrying it
        self._tags = {}  # key -> its tags

    def add(self, key, tags):
        tags = set(tags)
        self._tags.setdefault(key, set()).update(tags)
        for t in tags:
            self._keys.setdefault(t, set()).add(key)

    def keys_for(self, tags):
        """Every key carrying any of tags, sorted."""
        found = set()
        for t in tags:
            found |= self._keys.get(t, set())
        return sorted(found)

    def tags_of(self, key):
        return sorted(self._tags.get(key, ()))

    def forget(self, keys):
        for key in keys:
            for t in self._tags.pop(key, ()):
                held = self._keys.get(t)
                if held is None:
                    continue
                held.discard(key)
                if not held:
                    del self._keys[t]

    def clear(self):
        self._keys.clear()
        self._tags.clear()

    def __contains__(self, tag):
        return tag in self._keys

    def __len__(self):
        return len(self._tags)
