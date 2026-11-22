"""Pass when prices, invalidation, deletion, bulk refresh, the sweep and the cache report are per tenant, for
any ids."""
import importlib
import sys
from pathlib import Path

WORK = Path(sys.argv[1])
sys.path.insert(0, str(WORK))  # after the stdlib imports, so the work dir can't replace them

try:
    db = importlib.import_module("catalog.db")
    get_price = importlib.import_module("catalog.prices").get_price
    events = importlib.import_module("admin.events")
    refresh_all = importlib.import_module("admin.bulk").refresh_all
    sweep = importlib.import_module("jobs.sweep").sweep
    cached = importlib.import_module("reports.cached").cached_products

    db.set_price("a", "p1", 100)
    db.set_price("b", "p1", 200)
    ok = get_price("a", "p1") == 100 and get_price("b", "p1") == 200
    db.set_price("a", "p1", 150)
    events.on_price_changed("a", "p1")
    ok = ok and get_price("b", "p1") == 200 and cached("b") == ["p1"] and cached("a") == []
    ok = ok and get_price("a", "p1") == 150 and cached("a") == ["p1"]
    db.set_price("x:y", "p", 1)
    db.set_price("x", "y:p", 2)
    ok = ok and get_price("x:y", "p") == 1 and get_price("x", "y:p") == 2
    ok = ok and cached("x") == ["y:p"] and cached("x:y") == ["p"]
    db.set_price("b", "p2", 300)
    db.set_price("b", "p1", 250)
    refresh_all("b", ["p1", "p2"])
    ok = ok and cached("b") == ["p1", "p2"] and get_price("b", "p1") == 250 and get_price("a", "p1") == 150
    events.on_price_changed("x", "y:p")
    ok = ok and cached("x") == [] and cached("x:y") == ["p"]
    db.set_price("a", "p2", 50)
    get_price("a", "p2")
    sweep("a", ["p1"])
    ok = ok and cached("a") == ["p1"] and cached("b") == ["p1", "p2"]
    events.on_product_deleted("b", "p1")
    ok = ok and cached("b") == ["p2"] and cached("a") == ["p1"] and get_price("a", "p1") == 150
    db.set_price("a", "p1", 175)
    ok = ok and get_price("a", "p1") == 150  # still cached: deleting b's product mustn't evict a's price
    try:
        db.lookup("b", "p1")
        ok = False
    except KeyError:
        pass
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
