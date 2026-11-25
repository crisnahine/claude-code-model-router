"""The customer standings as CSV, for the finance team's spreadsheet."""
import csv

from . import customers, standings
from .ingest import read_sales
from .money import format_cents
from .regions import codes_in_order, read_regions

HEADER = ["rank", "customer", "total", "sales", "regions"]


def standings_rows(sales, regions):
    """[rank, customer, total, sales, regions] per customer, in the report's standings order. The total is
    written like the report writes it, without the thousands separator, so a spreadsheet reads it as a number."""
    rows = []
    for rank, t in standings.competition_ranks(customers.by_customer(sales)):
        rows.append([rank, t.name, format_cents(t.cents).replace(",", ""), t.count,
                     " ".join(codes_in_order(regions, t.regions))])
    return rows


def write_standings(sales_path, regions_path, out_path):
    regions = read_regions(regions_path)
    sales, _ = read_sales(sales_path, regions)
    rows = standings_rows(sales, regions)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    return len(rows)
