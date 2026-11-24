"""python3 -m plugins install BUNDLE PLUGINS_DIR | list PLUGINS_DIR | remove PLUGINS_DIR NAME | pack FOLDER OUT"""
import argparse
import sys

from archive.pack import pack

from . import registry
from .installer import BundleRejected, install_bundle, uninstall


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m plugins")
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("install")
    i.add_argument("bundle")
    i.add_argument("plugins_dir")
    li = sub.add_parser("list")
    li.add_argument("plugins_dir")
    r = sub.add_parser("remove")
    r.add_argument("plugins_dir")
    r.add_argument("name")
    p = sub.add_parser("pack", help="build a bundle from a plugin's source folder")
    p.add_argument("folder")
    p.add_argument("out")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            found = install_bundle(args.bundle, args.plugins_dir)
            print(f"installed {found.name} {found.version}")
        elif args.command == "list":
            for name, version in registry.installed(args.plugins_dir).items():
                print(f"{name} {version}")
        elif args.command == "remove":
            uninstall(args.plugins_dir, args.name)
            print(f"removed {args.name}")
        else:
            print(f"{pack(args.folder, args.out)} members written to {args.out}")
    except BundleRejected as e:
        print(f"rejected: {e}", file=sys.stderr)
        return 1
    except (KeyError, OSError) as e:
        print(f"plugins: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
