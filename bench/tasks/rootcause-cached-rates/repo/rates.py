import csv
import functools
import io

_DATA = {"eu": "sku,price\npen,100\nbook,1500\nlamp,4999\n", "us": "sku,price\npen,120\nbook,1700\nlamp,5499\n"}
LOADS = []  # regions read from the source, for the load report


def _load(region):
    LOADS.append(region)
    return list(csv.DictReader(io.StringIO(_DATA[region])))


@functools.lru_cache(maxsize=None)
def rate_table(region):
    """The price rows of a region, read from the source once per process. Callers must not change the rows."""
    rows = _load(region)
    for row in rows:
        row["price"] = int(row["price"])
    return rows
