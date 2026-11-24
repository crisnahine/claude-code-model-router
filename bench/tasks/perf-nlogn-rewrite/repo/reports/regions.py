"""The regions file: one `code,name` row per region, in the order the report prints them."""
import csv
from dataclasses import dataclass

from .errors import ReportError

HEADER = ["code", "name"]


@dataclass(frozen=True)
class Region:
    code: str
    name: str


def read_regions(path):
    regions = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        if next(reader, None) != HEADER:
            raise ReportError(f"{path}: the first line must be {','.join(HEADER)}")
        for number, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != 2 or not row[0].strip() or not row[1].strip():
                raise ReportError(f"{path}:{number}: expected code,name")
            code, name = row[0].strip(), " ".join(row[1].split())
            if any(r.code == code for r in regions):
                raise ReportError(f"{path}:{number}: region {code} is listed twice")
            regions.append(Region(code, name))
    return regions


def codes_in_order(regions, codes):
    """codes sorted the way the regions file lists them."""
    order = {r.code: n for n, r in enumerate(regions)}
    return sorted(codes, key=order.__getitem__)
