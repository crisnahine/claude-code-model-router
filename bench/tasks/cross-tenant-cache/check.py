"""Pass when prices, invalidation, deletion, bulk refresh, the sweep and the cache report are per tenant, for
any ids."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WORK = Path(sys.argv[1])
passcheck.use_work(WORK)

got = []
with passcheck.agent_code():
    db = importlib.import_module("catalog.db")
    get_price = importlib.import_module("catalog.prices").get_price
    events = importlib.import_module("admin.events")
    refresh_all = importlib.import_module("admin.bulk").refresh_all
    sweep = importlib.import_module("jobs.sweep").sweep
    cached = importlib.import_module("reports.cached").cached_products
    db.set_price("a", "p1", 100)
    db.set_price("b", "p1", 200)
    got += [get_price("a", "p1"), get_price("b", "p1")]
    db.set_price("a", "p1", 150)
    events.on_price_changed("a", "p1")
    got += [get_price("b", "p1"), cached("b"), cached("a")]
    got += [get_price("a", "p1"), cached("a")]
    db.set_price("x:y", "p", 1)
    db.set_price("x", "y:p", 2)
    got += [get_price("x:y", "p"), get_price("x", "y:p")]
    got += [cached("x"), cached("x:y")]
    db.set_price("b", "p2", 300)
    db.set_price("b", "p1", 250)
    refresh_all("b", ["p1", "p2"])
    got += [cached("b"), get_price("b", "p1"), get_price("a", "p1")]
    events.on_price_changed("x", "y:p")
    got += [cached("x"), cached("x:y")]
    db.set_price("a", "p2", 50)
    get_price("a", "p2")
    sweep("a", ["p1"])
    got += [cached("a"), cached("b")]
    events.on_product_deleted("b", "p1")
    got += [cached("b"), cached("a"), get_price("a", "p1")]
    db.set_price("a", "p1", 175)
    got.append(get_price("a", "p1"))  # still cached: deleting b's product mustn't evict a's price
    try:
        db.lookup("b", "p1")
        got.append("b's p1 is still in the catalog")
    except KeyError:
        got.append("deleted")
WANT = [100, 200, 200, ["p1"], [], 150, ["p1"], 1, 2, ["y:p"], ["p"], ["p1", "p2"], 250, 150, [], ["p"], ["p1"],
        ["p1", "p2"], ["p2"], ["p1"], 150, 150, "deleted"]
passcheck.finish(got == WANT, passcheck.differs(got, WANT))
