"""python3 -m ledger migrate SRC ACCOUNTS DST | reconcile V3 CONTROLS | statement V3 ACCOUNT CURRENCY
                  | find V3 [--account A] [--text WORDS] [--since UTC] [--until UTC] [--type TYPE]"""
import argparse
import sys

from . import reconcile, search, statement, v3
from .errors import FormatError, MigrationError
from .migrate_v3 import migrate


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("migrate", help="turn a v2 export into a v3 file")
    m.add_argument("src")
    m.add_argument("accounts")
    m.add_argument("dst")
    r = sub.add_parser("reconcile", help="check a v3 file against the control totals")
    r.add_argument("file")
    r.add_argument("controls")
    s = sub.add_parser("statement", help="print an account's statement")
    s.add_argument("file")
    s.add_argument("account")
    s.add_argument("currency")
    f = sub.add_parser("find", help="list the entries matching every filter given")
    f.add_argument("file")
    for name in ("account", "text", "since", "until", "type"):
        f.add_argument(f"--{name}")
    args = parser.parse_args(argv)
    try:
        if args.command == "migrate":
            print(f"{migrate(args.src, args.accounts, args.dst)} records written to {args.dst}")
        elif args.command == "reconcile":
            wrong = reconcile.differences(v3.read(args.file), reconcile.load_controls(args.controls))
            for account, currency, ledger, control in wrong:
                print(f"{account} {currency}: ledger {ledger}, control {control}")
            print("reconciled" if not wrong else f"{len(wrong)} balances differ")
            return 1 if wrong else 0
        elif args.command == "statement":
            print("\n".join(statement.lines(v3.read(args.file), args.account, args.currency)))
        else:
            found = search.find(v3.read(args.file), args.account, args.text, args.since, args.until, args.type)
            for r in found:
                print(v3.dump_line(r), end="")
            return 0 if found else 1
    except (OSError, FormatError, MigrationError) as e:
        print(f"ledger: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
