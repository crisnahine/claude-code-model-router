"""Cross-checks finance asks for before a report goes out: the same money counted three ways must agree."""
import re

from .errors import ReportError

MONEY = re.compile(r"-?[0-9][0-9,]*\.[0-9]{2}")


def _cents(text):
    return int(text.replace(",", "").replace(".", ""))


def totals(report):
    """(sum of the region totals, last running total in Days, sum of the standings) read back from the text."""
    regions = sum(_cents(m) for m in re.findall(r"^sales \d+, total (\S+),", report, re.M))
    days = report.split("== Days ==\n", 1)[1].split("\n\n", 1)[0].splitlines()
    running = _cents(MONEY.findall(days[-1])[-1]) if days and days != ["none"] else 0
    standings = report.split("== Customer standings ==\n", 1)[1].splitlines()
    people = sum(_cents(MONEY.findall(line)[0]) for line in standings if MONEY.search(line))
    return regions, running, people


def check(report):
    """Raise ReportError unless the regions, the days and the standings add up to the same total."""
    regions, running, people = totals(report)
    if not regions == running == people:
        raise ReportError(f"totals disagree: regions {regions}, days {running}, standings {people} (cents)")
