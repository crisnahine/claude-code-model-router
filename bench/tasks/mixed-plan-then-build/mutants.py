"""Broken builds of the feature, made by wrapping the work's own api.payments.handle_post at run time. Every name the
work defines stays, so a test that only imports names passes on them, and a test that sends requests fails."""
import copy

MUTANTS = {"ignores-the-key": "ignores the Idempotency-Key header and charges every retry",
           "no-body-check": "replays a reused key without comparing the body"}
# Runs a work dir's test module with one mutant installed: argv is work dir, test module, this file, mutant name.
LAUNCHER = ("import sys, unittest, importlib.util; sys.path.append(sys.argv[1]); "
            "spec = importlib.util.spec_from_file_location('_mutant', sys.argv[3]); "
            "mutant = importlib.util.module_from_spec(spec); spec.loader.exec_module(mutant); "
            "mutant.install(sys.argv[4]); "
            "del spec, mutant; unittest.main(module=None, argv=['unittest', sys.argv[2]])")


def _is_key(name):
    return isinstance(name, str) and name.lower() == "idempotency-key"


def _key(request):
    return next((v for k, v in (request.get("headers") or {}).items() if _is_key(k)), None)


def _database(conn):
    """The database file a connection is open on, so keys from one test's fresh database don't carry to the next."""
    try:
        return conn.execute("PRAGMA database_list").fetchone()[2] or id(conn)
    except Exception:
        return id(conn)


def install(name):
    import api.payments as api
    real = api.handle_post
    first = {}

    def ignores_the_key(conn, request):
        if isinstance(request, dict) and isinstance(request.get("headers"), dict):
            request = {**request, "headers": {k: v for k, v in request["headers"].items() if not _is_key(k)}}
        return real(conn, request)

    def no_body_check(conn, request):
        if not isinstance(request, dict) or not isinstance(request.get("headers") or {}, dict) or _key(request) is None:
            return real(conn, request)
        slot = (_database(conn), _key(request))
        if slot in first:
            request = {**request, "body": copy.deepcopy(first[slot])}
        got = real(conn, request)
        if slot not in first and isinstance(got, tuple) and got and got[0] == 201:
            first[slot] = copy.deepcopy(request.get("body"))
        return got

    api.handle_post = {"ignores-the-key": ignores_the_key, "no-body-check": no_body_check}[name]
