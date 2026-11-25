"""python3 -m reports SALES_CSV REGIONS_CSV [--out FILE] [--standings CSV_FILE] [--verify]"""
import argparse
import sys

from . import ReportError, render, verify
from .export import write_standings


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m reports", description="Print the monthly sales report.")
    parser.add_argument("sales", help="the sales export, a CSV file")
    parser.add_argument("regions", help="the regions file, a CSV file")
    parser.add_argument("--out", help="write the report here instead of to stdout")
    parser.add_argument("--standings", help="also write the customer standings to this CSV file")
    parser.add_argument("--verify", action="store_true", help="check the totals agree before writing anything")
    args = parser.parse_args(argv)
    try:
        text = render(args.sales, args.regions)
        if args.verify:
            verify.check(text)
        if args.standings:
            write_standings(args.sales, args.regions, args.standings)
    except (OSError, ReportError) as e:
        print(f"reports: {e}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    else:
        # The report is UTF-8 with \n line ends, whatever the console's own encoding and line ends are.
        sys.stdout.flush()
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
