import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)


class Abort(Exception):
    pass


def apply(app, uow, op):
    kind, sku, value = (op + [None])[:3]
    if kind == "price":
        app.pricing.set_price(uow, sku, value)
    elif kind == "name":
        app.catalog.rename(uow, sku, value)
    elif kind == "move":
        app.catalog.move(uow, sku, value)
    elif kind == "stock":
        app.inventory.adjust(uow, sku, value)
    elif kind == "read":
        app.storefront.page(sku)
    elif kind == "list":
        app.catalog.listing(sku)
    else:
        raise ValueError(f"unknown op {op!r}")


def step(app, s):
    if "save" in s:
        sku, fields = s["save"]
        app.admin.save_product(sku, **fields)
    elif "restock" in s:
        app.admin.restock(*s["restock"])
    elif "reprice" in s:
        app.admin.bulk_reprice(s["reprice"])
    else:
        try:
            with app.unit() as uow:
                for op in s["unit"]:
                    apply(app, uow, op)
                if s.get("fail"):
                    raise Abort()
        except Abort:
            pass


def reads(app):
    return sum(app.db.reads.values())


def everything(app, skus, categories):
    return {"pages": {sku: app.storefront.page(sku) for sku in skus},
            "listings": {c: app.catalog.listing(c) for c in categories},
            "facets": {sku: [app.catalog.product(sku), app.pricing.price(sku), app.inventory.stock(sku)] for sku in skus}}


def run(scenario):
    build_app = importlib.import_module("shop.app").build_app
    app = build_app(payload["products"])
    skus = [p["sku"] for p in payload["products"]]
    categories = payload["categories"]
    everything(app, skus, categories)
    for s in scenario["steps"]:
        step(app, s)
    before = reads(app)
    app.storefront.page(scenario["untouched"])
    other = reads(app) - before
    seen = everything(app, skus, categories)
    before = reads(app)
    everything(app, skus, categories)
    return dict(seen, error=None, reads_other=other, reads_again=reads(app) - before)


out = {}
for scenario in payload["scenarios"]:
    try:
        out[scenario["name"]] = run(scenario)
    except BaseException as e:
        out[scenario["name"]] = {"error": f"{type(e).__name__}: {e}"}
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
