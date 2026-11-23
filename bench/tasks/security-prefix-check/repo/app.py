from auth import tokens
from files import importer, server, thumbs


def handle(request):
    user = tokens.user(request["token"])
    if user is None:
        return 403, b"forbidden"
    path = request["path"]
    if request["method"] == "POST" and path == "/import":
        return 200, "\n".join(importer.import_bundle(user, request["body"])).encode()
    if path.startswith("/thumb/"):
        return 200, thumbs.thumbnail(path[len("/thumb/"):])
    if path.startswith("/imports/"):
        return 200, server.serve_import(user, path[len("/imports/"):])
    return 200, server.serve(path.lstrip("/"))
