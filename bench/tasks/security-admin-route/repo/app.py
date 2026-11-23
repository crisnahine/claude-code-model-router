from auth import session
from gateway import guard, router


def handle(request):
    user = session.user(request["cookie"])
    if not guard.allowed(user, request["path"]):
        return 403, None
    return 200, router.dispatch(user, request["path"], request.get("body"))
