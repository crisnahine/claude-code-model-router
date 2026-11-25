"""The report's sections, each as a list of lines."""
from . import customers, layout, standings
from .money import average_cents, format_cents
from .regions import codes_in_order

TOP_CUSTOMERS = 3


def header(counts, sales):
    lines = ["SALES REPORT",
             f"rows read {counts.read}, kept {counts.kept}, duplicates {counts.duplicates}, "
             f"rejected {counts.rejected}"]
    if sales:
        dates = [s.date for s in sales]
        lines.append(f"period {min(dates)} to {max(dates)}")
    else:
        lines.append("period none")
    return lines


def region(region, sales):
    """One region's section: its sales are every kept sale whose region code is region.code."""
    lines = [layout.heading(f"{region.name} ({region.code})")]
    if not sales:
        return lines + ["no sales"]
    total = sum(s.cents for s in sales)
    lines.append(f"sales {len(sales)}, total {format_cents(total)}, "
                 f"average {format_cents(average_cents(total, len(sales)))}")
    lines.append("top customers")
    ranked = standings.top(customers.by_customer(sales), TOP_CUSTOMERS)
    lines += layout.table([[rank, t.name, format_cents(t.cents)] for rank, t in ranked], "rlr")
    lines.append("products")
    lines += layout.table([[t.name, t.count, format_cents(t.cents)] for t in customers.by_product(sales)], "lrr")
    return lines


def days(sales):
    """Sales and total for each day that had any, oldest first, with the running total for the month."""
    lines = [layout.heading("Days")]
    if not sales:
        return lines + ["none"]
    count, cents = {}, {}
    for s in sales:
        count[s.date] = count.get(s.date, 0) + 1
        cents[s.date] = cents.get(s.date, 0) + s.cents
    rows, running = [], 0
    for date in sorted(count):
        running += cents[date]
        rows.append([date, count[date], format_cents(cents[date]), format_cents(running)])
    return lines + layout.table(rows, "lrrr")


def customer_standings(sales, regions):
    lines = [layout.heading("Customer standings")]
    totals = customers.by_customer(sales)
    if not totals:
        return lines + ["none"]
    rows = [[rank, t.name, format_cents(t.cents), t.count, " ".join(codes_in_order(regions, t.regions))]
            for rank, t in standings.competition_ranks(totals)]
    return lines + layout.table(rows, "rlrrl")
