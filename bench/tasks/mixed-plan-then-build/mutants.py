"""Broken builds of the feature, made by wrapping the work's own api.payments.handle_post at run time. Every name the
work defines stays, so a test that only imports names passes on them, and a test that sends requests fails."""
import copy

MUTANTS = {"ignores-the-key": "ignores the Idempotency-Key header and charges every retry",
           "no-body-check": "replays a reused key without comparing the body",
           "charges-the-retry": "charges a retry again and answers it as a new payment",
           "charges-every-repeat": "charges a repeated key again but sends back the right response",
           "charges-without-a-payment": "charges a repeated key again without recording a payment"}
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


def _slot(conn, request):
    if not isinstance(request, dict) or not isinstance(request.get("headers") or {}, dict) or _key(request) is None:
        return None
    return _database(conn), _key(request)


def install(name):
    import api.payments as api
    real = api.handle_post
    first = {}

    def ignores_the_key(conn, request):
        if isinstance(request, dict) and isinstance(request.get("headers"), dict):
            request = {**request, "headers": {k: v for k, v in request["headers"].items() if not _is_key(k)}}
        return real(conn, request)

    def remember(slot, request, got):
        if slot is not None and slot not in first and isinstance(got, tuple) and got and got[0] == 201:
            first[slot] = copy.deepcopy(request.get("body"))
        return got

    def no_body_check(conn, request):
        slot = _slot(conn, request)
        if slot in first:
            request = {**request, "body": copy.deepcopy(first[slot])}
        return remember(slot, request, real(conn, request))

    def charges_the_retry(conn, request):
        slot = _slot(conn, request)
        if slot in first and request.get("body") == first[slot]:
            return ignores_the_key(conn, request)
        return remember(slot, request, real(conn, request))

    def charges_every_repeat(conn, request):
        # The extra charge goes through the work's own path, so a test that mocks the gateway sees it too.
        slot = _slot(conn, request)
        if slot in first:
            ignores_the_key(conn, request)
        return remember(slot, request, real(conn, request))

    def charges_without_a_payment(conn, request):
        # Charged through the work's own path, so a mock wherever the work looks the gateway up sees it.
        slot = _slot(conn, request)
        if slot in first:
            before = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM payments").fetchone()[0]
            ignores_the_key(conn, request)
            conn.execute("DELETE FROM payments WHERE rowid > ?", (before,))
            conn.commit()
        return remember(slot, request, real(conn, request))

    api.handle_post = {"ignores-the-key": ignores_the_key, "no-body-check": no_body_check,
                       "charges-the-retry": charges_the_retry, "charges-every-repeat": charges_every_repeat,
                       "charges-without-a-payment": charges_without_a_payment}[name]
