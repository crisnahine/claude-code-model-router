from files.server import serve
from files.thumbs import thumb_path


def route(path, query):
    if path == "/file":
        return serve(query["name"])
    if path == "/thumb":
        return thumb_path(query["name"])
    return None
