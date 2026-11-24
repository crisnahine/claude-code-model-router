from dataclasses import dataclass


@dataclass
class Hook:
    url: str
    events: tuple
    secret: str = ""


_hooks = []


def register(hook):
    _hooks.append(hook)


def for_event(name):
    return [h for h in _hooks if name in h.events]
