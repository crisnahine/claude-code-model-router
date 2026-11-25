"""In-process events: modules register handlers with @on(name), and emit(name, **data) calls each one in turn."""
_handlers = {}


def on(name):
    def register(handler):
        _handlers.setdefault(name, []).append(handler)
        return handler
    return register


def emit(name, **data):
    for handler in _handlers.get(name, []):
        handler(**data)


def handlers(name):
    return list(_handlers.get(name, []))
