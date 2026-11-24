"""Pass when test_checkout.py, unchanged, passes and, run in a child after each of several write orders, every
product page, listing and service read shows what the database holds, while a page, a listing and the service
reads that no change touched, and a second read of anything, are still served from the cache."""
import copy
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
PRODUCTS = [
    {"sku": "A-1", "name": "Alpha", "category": "c1", "price": 1000, "units": 10},
    {"sku": "B-2", "name": "Bravo", "category": "c1", "price": 2000, "units": 5},
    {"sku": "C-3", "name": "Charlie", "category": "c2", "price": 3000, "units": 0},
    {"sku": "D-4", "name": "Delta", "category": "c2", "price": 4000, "units": 7},
]
CATEGORIES = ["c1", "c2", "c3", "c4"]
# Each scenario runs on a fresh shop with every page and listing cached; "untouched" names a sku and "quiet" a
# category that no step changes.
SCENARIOS = [
    {"name": "price then name", "untouched": "B-2", "quiet": "c2",
     "steps": [{"save": ["A-1", {"price": 900, "name": "Alpha Two"}]}]},
    {"name": "name then price", "untouched": "D-4", "quiet": "c2",
     "steps": [{"unit": [["name", "A-1", "Alpha Two"], ["price", "A-1", 900]]}]},
    {"name": "stock then price", "untouched": "C-3", "quiet": "c1",
     "steps": [{"unit": [["stock", "A-1", -3], ["price", "A-1", 950]]}]},
    {"name": "price then stock", "untouched": "A-1", "quiet": "c1",
     "steps": [{"unit": [["price", "B-2", 2100], ["stock", "B-2", 4]]}]},
    {"name": "move then rename", "untouched": "D-4", "quiet": "c2",
     "steps": [{"unit": [["move", "A-1", "c3"], ["name", "A-1", "Alpha Moved"]]}]},
    {"name": "rename then move", "untouched": "A-1", "quiet": "c3",
     "steps": [{"unit": [["name", "B-2", "Bravo X"], ["move", "B-2", "c2"]]}]},
    {"name": "every facet of one sku", "untouched": "B-2", "quiet": "c3",
     "steps": [{"unit": [["name", "C-3", "Charlie Z"], ["move", "C-3", "c1"], ["price", "C-3", 3100],
                         ["stock", "C-3", 2]]}]},
    {"name": "two skus in one unit", "untouched": "C-3", "quiet": "c1",
     "steps": [{"unit": [["price", "A-1", 1100], ["name", "D-4", "Delta Q"], ["stock", "A-1", -1],
                         ["price", "D-4", 4100]]}]},
    {"name": "a read inside the unit", "untouched": "B-2", "quiet": "c2",
     "steps": [{"unit": [["price", "A-1", 1200], ["read", "A-1"], ["list", "c1"], ["name", "A-1", "Alpha R"]]}]},
    {"name": "separate units", "untouched": "D-4", "quiet": "c2",
     "steps": [{"save": ["A-1", {"price": 1300}]}, {"save": ["A-1", {"name": "Alpha S"}]}]},
    {"name": "a unit that failed", "untouched": "B-2", "quiet": "c1",
     "steps": [{"unit": [["price", "B-2", 9999], ["name", "B-2", "Nope"]], "fail": True}]},
    {"name": "restock then bulk reprice", "untouched": "C-3", "quiet": "c2",
     "steps": [{"restock": ["D-4", 3]}, {"reprice": {"A-1": 800, "D-4": 3900}}]},
    {"name": "move twice", "untouched": "B-2", "quiet": "c4",
     "steps": [{"unit": [["move", "A-1", "c2"], ["move", "A-1", "c3"]]}]},
]
OPS = {"price": "price", "name": "name", "move": "category", "stock": "units"}


def expected(scenario):
    shop = {p["sku"]: copy.deepcopy(p) for p in PRODUCTS}
    for s in scenario["steps"]:
        if "save" in s:
            sku, fields = s["save"]
            shop[sku].update(fields)
        elif "restock" in s:
            shop[s["restock"][0]]["units"] += s["restock"][1]
        elif "reprice" in s:
            for sku, cents in s["reprice"].items():
                shop[sku]["price"] = cents
        elif not s.get("fail"):
            for kind, sku, value in (op for op in s["unit"] if op[0] in OPS):
                shop[sku][OPS[kind]] = shop[sku][OPS[kind]] + value if kind == "stock" else value
    pages = {sku: {"sku": sku, "name": p["name"], "category": p["category"], "price": p["price"], "stock": p["units"],
                   "in_stock": p["units"] > 0} for sku, p in shop.items()}
    listings = {c: [[sku, p["name"]] for sku, p in sorted(shop.items()) if p["category"] == c] for c in CATEGORIES}
    facets = {sku: [{"sku": sku, "name": p["name"], "category": p["category"]}, p["price"], p["units"]]
              for sku, p in shop.items()}
    return {"error": None, "pages": pages, "listings": listings, "facets": facets}


def wrong(name, got, want):
    """Why one scenario's result is wrong, or None."""
    if not isinstance(got, dict) or got.get("error") is not None:
        return f"after {name}, the shop raised {(got or {}).get('error')!r}"
    for field, label in (("pages", "page"), ("listings", "listing"), ("facets", "service reads for")):
        for item, value in want[field].items():
            seen = (got.get(field) or {}).get(item)
            if seen != value:
                return f"after {name}, {label} {item} is {seen!r}, expected {value!r}"
    scenario = next(s for s in SCENARIOS if s["name"] == name)
    sku, category = scenario["untouched"], scenario["quiet"]
    labels = [f"page {sku}", f"the catalog's product {sku}", f"the price of {sku}", f"the stock of {sku}",
              f"listing {category}"]
    counts = got.get("reads_other")
    if not isinstance(counts, list) or len(counts) != len(labels):
        return f"after {name}, the probe counted no reads for what no change touched"
    for label, count in zip(labels, counts):
        if count != 0:
            return f"after {name}, reading {label}, which no change touched, went to the database"
    if got.get("reads_again") != 0:
        return f"after {name}, reading everything a second time went to the database"
    return None


work = Path(sys.argv[1])
if not passcheck.tests_pass(work, HERE / "repo", "test_checkout.py"):
    passcheck.finish(False, "test_checkout.py doesn't pass unchanged")
got = passcheck.probe(work, HERE / "probe.py", {"products": PRODUCTS, "categories": CATEGORIES,
                                                "scenarios": SCENARIOS})
if not isinstance(got, dict):
    passcheck.finish(False, "the probe gave no result")
for scenario in SCENARIOS:
    reason = wrong(scenario["name"], got.get(scenario["name"]), expected(scenario))
    if reason:
        passcheck.finish(False, reason[:300])
passcheck.finish(True)
