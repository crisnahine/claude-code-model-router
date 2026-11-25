"""Builds the whole report."""
from . import sections
from .ingest import read_sales
from .regions import read_regions


def render(sales_path, regions_path):
    """The report as text: the header, one section per region in the regions file's order (a region with no
    sales still gets one), the days, then every customer's standing. Sections are separated by a blank line."""
    regions = read_regions(regions_path)
    sales, counts = read_sales(sales_path, regions)
    blocks = [sections.header(counts, sales)]
    by_region = {}
    for sale in sales:
        by_region.setdefault(sale.region, []).append(sale)
    for region in regions:
        if region.code in by_region:
            blocks.append(sections.region(region, by_region[region.code]))
    blocks.append(sections.days(sales))
    blocks.append(sections.customer_standings(sales, regions))
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"
