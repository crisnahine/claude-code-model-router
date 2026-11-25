import re


class Request:
    def __init__(self, method, path, query=None, body=None, user=None):
        self.method, self.path = method, path
        self.query, self.body, self.user = query or {}, body or {}, user
        self.params = {}


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, handler):
        regex = "^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$"
        self.routes.append((method, re.compile(regex), handler))

    def dispatch(self, request):
        for method, regex, handler in self.routes:
            match = regex.match(request.path)
            if match and method == request.method:
                request.params = match.groupdict()
                return handler(request)
        return 404, {"error": "not found"}
