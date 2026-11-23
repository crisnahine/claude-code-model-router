import posixpath
import random
import time
from urllib.parse import unquote

from gateway import admin, etag, files, hooks, releases


def delay(attempt):
    return min(30, 2 ** attempt) * random.uniform(0.5, 1.5)


def call(handler, *args):
    for attempt in range(3):
        try:
            return handler(*args)
        except OSError:
            time.sleep(delay(attempt))
    return handler(*args)


def dispatch(user, raw, body):
    path = unquote(raw.split("?", 1)[0])
    # links from the old mailer arrive encoded twice
    if "%" in path:
        path = unquote(path)
    path = posixpath.normpath("/" + path.lstrip("/")) + "/"
    if path == "/admin/users/":
        return admin.list_users()
    if path == "/admin/users/delete/":
        return admin.delete_user(body["id"])
    if path == "/admin/lookup/":
        return admin.find(body["kind"], body["id"])
    if path.startswith("/files/"):
        data = call(files.read, path[len("/files/"):-1])
        return {"etag": etag.etag(data), "body": data}
    if path == "/hooks/push/":
        return hooks.receive(body["payload"], body["signature"])
    if path == "/releases/changelog/":
        return releases.changelog(body["ref"])
    return None
