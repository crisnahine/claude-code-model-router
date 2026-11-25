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
    for region in regions:
        blocks.append(sections.region(region, [s for s in sales if s.region == region.code]))
    blocks.append(sections.days(sales))
    blocks.append(sections.customer_standings(sales, regions))
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"
