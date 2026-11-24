"""Reading the sales export: which rows count, and how they are counted."""
import csv
import datetime
import re
from dataclasses import dataclass

from . import money, names
from .errors import ReportError

HEADER = ["txn_id", "date", "region", "customer", "product", "amount"]
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Sale:
    txn_id: str
    date: str
    region: str
    customer: str
    customer_key: str
    product: str
    product_key: str
    cents: int


@dataclass
class Counts:
    read: int = 0
    kept: int = 0
    duplicates: int = 0
    rejected: int = 0


def parse_row(record, region_codes):
    """A Sale for one CSV record, or None when the record is rejected: the wrong number of fields, an empty
    field, a date that isn't a real YYYY-MM-DD day, a region the regions file doesn't list, or a bad amount."""
    if len(record) != len(HEADER):
        return None
    txn_id, date, region, customer, product, amount = (field.strip() for field in record)
    customer, product = names.clean(customer), names.clean(product)
    if not (txn_id and customer and product) or region not in region_codes:
        return None
    if not DATE.fullmatch(date):
        return None
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        return None
    cents = money.parse_cents(amount)
    if cents is None:
        return None
    return Sale(txn_id, date, region, customer, names.key(customer), product, names.key(product), cents)


def read_sales(path, regions):
    """The kept sales in file order, and the counts. A row that passes every check but repeats the txn_id of an
    earlier kept row is a duplicate and is dropped. A row that fails a check is rejected, even when its txn_id was
    seen, and its txn_id doesn't count as seen."""
    region_codes = {r.code for r in regions}
    counts = Counts()
    sales = []
    seen = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None or [h.strip() for h in header] != HEADER:
            raise ReportError(f"{path}: the first line must be {','.join(HEADER)}")
        for record in reader:
            counts.read += 1
            sale = parse_row(record, region_codes)
            if sale is None:
                counts.rejected += 1
                continue
            if sale.txn_id in seen:
                counts.duplicates += 1
                continue
            seen.append(sale.txn_id)
            sales.append(sale)
    counts.kept = len(sales)
    return sales, counts
