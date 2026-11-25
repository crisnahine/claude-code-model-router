"""Write a sales export of any size for trying the report on: python3 make_sales.py OUT.csv [ROWS] [SEED]

The rows look like a real month: about one customer for every five sales, a few hundred products, a retried row
now and then (the same txn_id again) and a few rows the report rejects.
"""
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIRST = ["Ana", "Ben", "Chloé", "Dmitri", "Eve", "Farah", "Goran", "Hana", "Ivo", "Jun", "Kofi", "Lena", "Mateo",
         "Nia", "Omar", "Priya", "Quinn", "Rosa", "Sven", "Tariq", "Uma", "Viktor", "Wen", "Xavi", "Yara", "Zoltan"]
LAST = ["Abbott", "Bauer", "Castro", "Dubois", "Eriksen", "Fischer", "García", "Haddad", "Ito", "Jensen", "Kowalski",
        "Lindqvist", "Moreau", "Nakamura", "Okafor", "Petrov", "Quist", "Rossi", "Schmidt", "Tanaka", "Ueda",
        "Varga", "Weber", "Xu", "Yilmaz", "Zielinski"]
GOODS = ["lamp", "mug", "pan", "rug", "vase", "kettle", "chair", "desk", "shelf", "clock", "towel", "pillow"]
STYLES = ["oak", "steel", "linen", "glass", "brass", "wool", "clay", "walnut", "copper", "bamboo"]


def region_codes(path=HERE / "sample" / "regions.csv"):
    with open(path, newline="", encoding="utf-8") as f:
        return [row[0] for row in list(csv.reader(f))[1:] if row]


def rows(count, seed=1, codes=None):
    rng = random.Random(seed)
    codes = codes or region_codes()
    people = max(1, count // 5)
    customers = [f"{rng.choice(FIRST)} {rng.choice(LAST)} {n}" for n in range(people)]
    products = [f"{style} {good}" for good in GOODS for style in STYLES]
    out = []
    for n in range(count):
        if out and rng.random() < 0.01:
            out.append(list(rng.choice(out)))  # a retried row
            continue
        amount = f"{rng.randint(100, 50000) / 100:.2f}"
        if rng.random() < 0.003:
            amount = rng.choice(["", "n/a", "1e3", "12.345"])
        out.append([f"T{n:07d}", f"2024-05-{rng.randint(1, 31):02d}", rng.choice(codes), rng.choice(customers),
                    rng.choice(products), amount])
    return out


def write(path, count, seed=1, codes=None):
    count = min(count, 20_000)  # big enough to see the shape of the report
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["txn_id", "date", "region", "customer", "product", "amount"])
        writer.writerows(rows(count, seed, codes))


if __name__ == "__main__":
    write(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 200_000, int(sys.argv[3]) if len(sys.argv) > 3 else 1)
