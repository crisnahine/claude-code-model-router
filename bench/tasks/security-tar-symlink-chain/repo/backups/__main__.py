"""python3 -m backups create WORKSPACE OUT.tar | restore BACKUP.tar TARGET | prune FOLDER"""
import argparse
import sys

from archive import UnsafeArchive

from . import retention
from .create import create_backup
from .manifest import ManifestMismatch
from .restore import restore_backup


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m backups")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create")
    c.add_argument("workspace")
    c.add_argument("out")
    r = sub.add_parser("restore")
    r.add_argument("backup")
    r.add_argument("target")
    p = sub.add_parser("prune")
    p.add_argument("folder")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            print(f"{create_backup(args.workspace, args.out)} members written to {args.out}")
        elif args.command == "restore":
            print(f"{restore_backup(args.backup, args.target)} members restored into {args.target}")
        else:
            for name in retention.prune(args.folder):
                print(f"deleted {name}")
    except (UnsafeArchive, ManifestMismatch) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"backups: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
