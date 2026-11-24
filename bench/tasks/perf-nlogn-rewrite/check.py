"""Pass when reports.render, python3 -m reports with --out, --standings and --verify, and python3 -m reports printing
to stdout, run in a child that rewrites the same two input files for every case, write byte for byte what the
shipped code writes on awkward inputs (ties, regions with no sales, spellings that casefold
together, decomposed accents, half cents, reused ids, quoted fields, padded headers), render builds the report for
20,000 rows in a few seconds, test_report.py passes unchanged, and each of them writes the right output for 200,000
rows, with every awkward row above spread through them, in at most 10 seconds."""
import csv
import datetime
import hashlib
import importlib.util
import io
import random
import re
import sys
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe.py"
HEADER = "txn_id,date,region,customer,product,amount\n"
BUDGET = 10.0
REGIONS = "code,name\nN,North\nQ,Åland\nE,East\nI,Île-de-France\nS,South\nZ,Empty Quarter\n"


def sales(*rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return HEADER + out.getvalue()


def row(n, customer, amount, region="N", product="oak lamp", date="2024-05-01"):
    return [f"T{n}", date, region, customer, product, amount]


# One edge each, before the mixed cases, so a reason names the edge a wrong answer missed.
HAND = [
    ("a region with no sales", REGIONS, sales(row(1, "Ana", "30.00"), row(2, "Bo", "20.00", "E"),
                                              row(3, "Cy", "10.00", "S"))),
    ("a tie at third place", REGIONS, sales(*[row(n, name, amount) for n, (name, amount) in enumerate(
        [("Ana", "50.00"), ("Bo", "40.00"), ("Cy", "30.00"), ("Dee", "30.00"), ("Eli", "30.00"), ("Fay", "20.00")])])),
    ("spellings that casefold together", REGIONS, sales(
        row(1, "Straße", "10.00"), row(2, "STRASSE", "5.00"), row(3, "strasse", "1.00", "E"),
        row(4, "ﬁona", "7.00"), row(5, "Fiona", "8.00"), row(6, "Zoë", "2.00", product="OAK  LAMP"),
        row(7, "ZOË", "2.00", product="Oak Lamp"))),
    ("decomposed accents", REGIONS, sales(
        row(1, "José", "4.00"), row(2, "José", "6.00"), row(3, "  José   Ruiz ", "1.00"),
        row(4, "José Ruiz", "1.00", product="café mug"), row(5, "Émile", "3.00", product="café mug"))),
    ("an average that ends in half a cent", REGIONS, sales(
        row(1, "Ana", "0.01"), row(2, "Bo", "0.00"), row(3, "Cy", "-0.01", "E"), row(4, "Dee", "0.00", "E"),
        row(5, "Eli", "0.05", "S"), row(6, "Fay", "0.10", "S"))),
    ("a rejected row's id used again", REGIONS, sales(
        ["T1", "2024-05-01", "N", "Ana", "oak lamp", "n/a"], ["T1", "2024-05-02", "N", "Bo", "oak lamp", "9.00"],
        ["T2", "2024-02-30", "N", "Cy", "oak lamp", "1.00"], ["T2", "2024-05-03", "E", "Dee", "oak lamp", "2.00"])),
    ("a kept id used again by a rejected row", REGIONS, sales(
        row(1, "Ana", "5.00"), row(1, "Bo", "n/a"), row(2, "Cy", "3.00"), row(2, "Dee", "3.00", date="2024-02-30"),
        row(3, "Eli", "1.00"), row(3, "Fay", "1.00", region="X"), row(4, "Gus", "2.00"), row(4, "", "2.00"))),
    # csv.reader and splitting each line on commas read these differently.
    ("quoted fields", REGIONS, HEADER + 'T1,2024-05-01,N,"Lima, Ana",oak lamp,3.00\n'
     'T2,2024-05-01,N,"Bo ""the"" Cat","lamp, oak",4.00\nT3,2024-05-02,E,"Cy",oak lamp,"5.00"\n'
     'T4,2024-05-02,E,"Dee\nRuiz",oak lamp,6.00\r\nT5,2024-05-03,S," Eli ",oak lamp,1.00\n'),
    ("a duplicate with a different amount", REGIONS, sales(
        row(1, "Ana", "5.00"), row(1, "Ana", "500.00"), row(2, "Bo", "6.00"), row(2, "Bo", "6.00", "E"))),
    ("no rows at all", REGIONS, HEADER),
    ("a header with spaces around its names", REGIONS,
     " txn_id , date,region ,customer,  product,amount \n" + sales(row(1, "Ana", "3.00")).split("\n", 1)[1]),
    ("region names with extra spaces", "code,name\n N ,  North   Side \nE,East\t\n S,\tSouth \n",
     sales(row(1, "Ana", "3.00"), row(2, "Bo", "2.00", "E"), row(3, "Cy", "1.00", "S"))),
]

CUSTOMERS = ["Straße", "STRASSE", "strasse", "José", "José", "  Ana   Lima ", "ana lima", "ANA LIMA", "Zoë",
             "zoë", "İlker", "ilker", "ﬁona", "Fiona", "Øyvind", "Ægir", "李雷", "Émile", "émile", "Bo", "bo", "Cy",
             "Lima, Ana", "lima, ana", 'Bo "the" Cat', "Dee\nRuiz"]
PRODUCTS = ["oak lamp", "Oak Lamp", "OAK  LAMP", "mug", "Mug", "Ölkanne", "ölkanne", "pan", "rug", "café mug",
            "lamp, oak", 'the "big" mug']
AMOUNTS = ["5.00", "5", "10.00", "0.50", "-2.50", "2.5", "0.01", "-0.01", "0.00", "05.00", "-0", "12.34"]
BAD_AMOUNTS = ["1,000", "1.234", "+5", ".5", "5.", "1e3", "", "five", "-", "1 000"]
DATES = ["2024-05-01", "2024-05-31", "2024-02-29", "2024-05-15"]
BAD_DATES = ["2023-02-29", "2024-13-01", "2024-5-1", "20240501", "2024-W18-3", "", "2024-05-01T10:00"]


def mixed(seed):
    rng = random.Random(seed)
    rows, ids = [], []
    for n in range(150):
        record = [f"T{n:04d}", rng.choice(DATES), rng.choice("NNQEEIS"), rng.choice(CUSTOMERS),
                  rng.choice(PRODUCTS), rng.choice(AMOUNTS)]
        roll = rng.random()
        if roll < 0.06:
            record[5] = rng.choice(BAD_AMOUNTS)
        elif roll < 0.10:
            record[1] = rng.choice(BAD_DATES)
        elif roll < 0.12:
            record[2] = rng.choice(["X", "n", ""])
        elif roll < 0.14:
            record = record[:5] if rng.random() < 0.5 else record + ["extra"]
        elif roll < 0.24 and ids:
            record[0] = rng.choice(ids)
        if rng.random() < 0.1:
            record = [f" {field} " for field in record]
        ids.append(record[0].strip() if record else "")
        rows.append(record)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows(rows)
    return HEADER + out.getvalue() + ("\n" if seed % 2 else "")


CASES = HAND + [(f"mixed case {seed}", REGIONS, mixed(seed)) for seed in range(1, 7)]


def big(rows, seed):
    """A month at full size: about a customer for every five sales, some unicode names, retried rows, bad rows."""
    rng = random.Random(seed)
    first = ["Ana", "Bo", "Chloé", "José", "Straße", "Zoë", "Øyvind", "Li", "Nia", "Omar", "Priya", "Sven"]
    customers = [f"{rng.choice(first)} {n}" for n in range(rows // 5)]
    customers += [c.upper() for c in rng.sample(customers, 500)]
    products = [f"{a} {b}" for a in ("oak", "steel", "linen", "Glass", "brass") for b in ("lamp", "mug", "pan", "rug")]
    lines = []
    for n in range(rows):
        if lines and rng.random() < 0.01:
            lines.append(rng.choice(lines))
            continue
        amount = f"{rng.randint(-2000, 60000) / 100:.2f}" if rng.random() > 0.002 else "n/a"
        lines.append(f"T{n},2024-05-{rng.randint(1, 31):02d},{rng.choice('NQEIS')},{rng.choice(customers)},"
                     f"{rng.choice(products)},{amount}\n")
    return HEADER + "".join(lines)


def with_edges(text, seed):
    """text with the rows of every hand-made and mixed case spread through it in order, under ids of their own."""
    rng = random.Random(seed)
    lines = text.splitlines(True)
    extras = []
    for n, (_, _, case) in enumerate(CASES):
        for record in list(csv.reader(io.StringIO(case, newline="")))[1:]:
            if record:
                record[0] = record[0].replace("T", f"C{n}T", 1)
                out = io.StringIO()
                csv.writer(out, lineterminator="\n").writerow(record)
                extras.append(out.getvalue())
    slots = sorted(rng.randrange(1, len(lines) + 1) for _ in extras)
    merged, last = [], 0
    for slot, extra in zip(slots, extras):
        merged += lines[last:slot]
        merged.append(extra)
        last = slot
    return "".join(merged + lines[last:])


def clean(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


def money(cents):
    whole, part = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{whole:,}.{part:02d}"


def table(rows, align):
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(r[n]) for r in cells) for n in range(len(align))]
    return [("  " + "  ".join(c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(r, widths, align))).rstrip()
            for r in cells]


def totals(kept, field):
    """{key: [first spelling, cents, count, region codes]} in first-seen order."""
    out = {}
    for sale in kept:
        name = sale[field]
        entry = out.setdefault(name.casefold(), [name, 0, 0, set()])
        entry[1] += sale["cents"]
        entry[2] += 1
        entry[3].add(sale["region"])
    return out


def ranked(entries):
    """Competition ranks by cents, biggest first, ties in name order."""
    entries = sorted(entries, key=lambda e: (-e[1], e[0].casefold(), e[0]))
    out, rank = [], 0
    for place, e in enumerate(entries, 1):
        if place == 1 or e[1] != entries[place - 2][1]:
            rank = place
        out.append((rank, e))
    return out


def reference(sales_text, regions_text):
    """The report and the standings CSV the shipped code writes, built in one pass over the rows."""
    regions = [(r[0].strip(), " ".join(r[1].split())) for r in list(csv.reader(io.StringIO(regions_text)))[1:] if r]
    order = {code: n for n, (code, _) in enumerate(regions)}
    read = rejected = duplicates = 0
    seen, kept = set(), []
    for record in list(csv.reader(io.StringIO(sales_text, newline="")))[1:]:
        read += 1
        fields = [f.strip() for f in record]
        if len(fields) != 6:
            rejected += 1
            continue
        txn, date, region, customer, product, amount = fields
        customer, product = clean(customer), clean(product)
        good = txn and customer and product and region in order and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date)
        if good:
            try:
                datetime.date.fromisoformat(date)
            except ValueError:
                good = False
        if not good or not re.fullmatch(r"-?[0-9]+(\.[0-9]{1,2})?", amount):
            rejected += 1
            continue
        if txn in seen:
            duplicates += 1
            continue
        seen.add(txn)
        kept.append({"date": date, "region": region, "customer": customer, "product": product,
                     "cents": int(Decimal(amount) * 100)})
    blocks = [["SALES REPORT", f"rows read {read}, kept {len(kept)}, duplicates {duplicates}, rejected {rejected}",
               f"period {min(s['date'] for s in kept)} to {max(s['date'] for s in kept)}" if kept else "period none"]]
    by_region = {code: [] for code in order}
    for sale in kept:
        by_region[sale["region"]].append(sale)
    for code, name in regions:
        mine = by_region[code]
        block = [f"== {name} ({code}) =="]
        if not mine:
            blocks.append(block + ["no sales"])
            continue
        total = sum(s["cents"] for s in mine)
        average = int((Decimal(total) / Decimal(len(mine))).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        block.append(f"sales {len(mine)}, total {money(total)}, average {money(average)}")
        block.append("top customers")
        block += table([[r, e[0], money(e[1])] for r, e in ranked(totals(mine, "customer").values()) if r <= 3], "rlr")
        block.append("products")
        products = sorted(totals(mine, "product").values(), key=lambda e: (-e[1], e[0].casefold(), e[0]))
        block += table([[e[0], e[2], money(e[1])] for e in products], "lrr")
        blocks.append(block)
    days = {}
    for sale in kept:
        entry = days.setdefault(sale["date"], [0, 0])
        entry[0] += 1
        entry[1] += sale["cents"]
    rows, running = [], 0
    for date in sorted(days):
        running += days[date][1]
        rows.append([date, days[date][0], money(days[date][1]), money(running)])
    blocks.append(["== Days =="] + (table(rows, "lrrr") if rows else ["none"]))
    standings = ["== Customer standings =="]
    everyone = ranked(totals(kept, "customer").values())
    standings += table([[r, e[0], money(e[1]), e[2], " ".join(sorted(e[3], key=order.__getitem__))]
                        for r, e in everyone], "rlrrl") if everyone else ["none"]
    blocks.append(standings)
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(
        [["rank", "customer", "total", "sales", "regions"]] +
        [[r, e[0], money(e[1]).replace(",", ""), e[2], " ".join(sorted(e[3], key=order.__getitem__))]
         for r, e in everyone])
    return "\n\n".join("\n".join(b) for b in blocks) + "\n", out.getvalue()


def first_difference(got, want):
    a, b = got.split("\n"), want.split("\n")
    for n, (x, y) in enumerate(zip(a, b), 1):
        if x != y:
            return f"line {n} is {x!r}, expected {y!r}"
    return f"{len(a)} lines, expected {len(b)}"


work = Path(sys.argv[1])
for via, what in (("render", "reports.render"), ("command", "python3 -m reports")):
    payload = {"cases": [{"sales": s, "regions": r} for _, r, s in CASES], "via": via}
    want = passcheck.probe(HERE / "repo", PROBE, payload)
    if not isinstance(want, list) or len(want) != len(CASES) or any(w["error"] for w in want):
        raise RuntimeError(f"the shipped report failed on the check's own inputs: {want!r}"[:300])
    got = passcheck.probe(work, PROBE, payload)
    if not isinstance(got, list) or len(got) != len(CASES):
        passcheck.finish(False, f"{what} didn't run: {got!r}"[:300])
    for (name, _, _), g, w in zip(CASES, got, want):
        if g.get("error"):
            passcheck.finish(False, f"on {name}, {what} raised {g['error']}"[:300])
        if g.get("text") != w["text"]:
            # The probe joins the command's report, standings CSV and printed report with a NUL.
            parts = [str(g.get("text")).split("\0"), w["text"].split("\0")]
            labels = ("", "the standings CSV, ", "on stdout, ")
            if len(parts[0]) != len(parts[1]):
                passcheck.finish(False, f"on {name}, {what} wrote a NUL into its output")
            for label, a, b in zip(labels, *parts):
                if a != b:
                    passcheck.finish(False, f"on {name}, {what} wrote {label}{first_difference(a, b)}"[:300])
# The 20,000-row run only ends a slow answer early; the 200,000-row run decides, whatever the machine's speed.
gate = passcheck.probe(work, PROBE, {"cases": [{"sales": big(20_000, 5), "regions": REGIONS}], "digest": True},
                       timeout=4)
if not gate:
    passcheck.finish(False, "too slow: 20,000 rows took more than 4 seconds")
text = with_edges(big(200_000, 11), 13)
full = passcheck.probe(work, PROBE, {"cases": [{"sales": text, "regions": REGIONS}], "digest": True},
                       timeout=int(BUDGET) + 5)
if not full:
    passcheck.finish(False, f"too slow: 200,000 rows took far more than {BUDGET:g} seconds")
if full[0]["error"]:
    passcheck.finish(False, f"on 200,000 rows, reports.render raised {full[0]['error']}"[:300])
if full[0]["seconds"] > BUDGET:
    passcheck.finish(False, f"too slow: 200,000 rows took {full[0]['seconds']:.1f} seconds")
report, table = reference(text, REGIONS)
if full[0]["sha256"] != hashlib.sha256(report.encode("utf-8")).hexdigest():
    passcheck.finish(False, "the report for 200,000 rows differs from the shipped code's")
# The standings export reads the sales again, so a rewrite that only speeds up render leaves it as slow as it was.
full = passcheck.probe(work, PROBE, {"cases": [{"sales": text, "regions": REGIONS}], "digest": True, "via": "command"},
                       timeout=int(BUDGET) + 5)
COMMAND = "python3 -m reports --standings on 200,000 rows"
if not full:
    passcheck.finish(False, f"too slow: {COMMAND} took far more than {BUDGET:g} seconds")
if full[0]["error"]:
    passcheck.finish(False, f"{COMMAND} raised {full[0]['error']}"[:300])
if full[0]["seconds"] > BUDGET:
    passcheck.finish(False, f"too slow: {COMMAND} took {full[0]['seconds']:.1f} seconds")
if full[0]["sha256"] != hashlib.sha256((report + "\0" + table).encode("utf-8")).hexdigest():
    passcheck.finish(False, f"{COMMAND} writes a report or standings CSV unlike the shipped code's")
passcheck.finish(passcheck.tests_pass(work, HERE / "repo", "test_report.py"), "test_report.py doesn't pass unchanged")
