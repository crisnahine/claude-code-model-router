"""Command line: hook commands read hook JSON on stdin; the rest serve skills and CI.

  hooks      session-start, user-prompt, pre-agent, post-agent, agent-failed, agent-stopped,
             post-edit, stop, model-switch, refresh-cache
  policy     the routing policy as it stands for this user and project
  models     what each registry model really runs as, and what is unavailable
             (--reset forgets learned availability and feedback; --json prints model-router.models/1)
  explain    what the router would make of a task: explain [--agent NAME] TEXT
  refresh    fetch upstream (--force) and show what changed; status shows the cache;
             ack marks it reviewed
  usage      the ledger by agent and model: usage [DAYS] [--json for model-router.usage/1]
  upstream-check --baseline FILE [--write]   CI: compare live upstream with a baseline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import hooks, learn, report, settings, store, upstream
from . import config as cfg


# Hook fields the handlers use as text: as file keys, paths, or things to match against.
_TEXT_FIELDS = ("session_id", "cwd", "transcript_path", "prompt", "prompt_id", "agent_id", "agent_type",
                "model", "to_model", "error")


def _well_formed(hook_input: object) -> dict:
    """Hook input with anything of the wrong type dropped, so a handler sees a field as
    text or not at all."""
    if not isinstance(hook_input, dict):
        return {}
    return {key: value for key, value in hook_input.items()
            if key not in _TEXT_FIELDS or isinstance(value, str)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="model-router")
    parser.add_argument("--data-dir", help="plugin data directory; skills pass ${CLAUDE_PLUGIN_DATA}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in (*hooks.HANDLERS, "policy", "ack"):
        sub.add_parser(name)
    p = sub.add_parser("models")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("explain")
    p.add_argument("--agent")
    p.add_argument("text", nargs="*")
    sub.add_parser("refresh").add_argument("--force", action="store_true")
    sub.add_parser("status").add_argument("--json", action="store_true")
    p = sub.add_parser("usage")
    p.add_argument("days", nargs="?", default="7")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("upstream-check")
    p.add_argument("--baseline", required=True)
    p.add_argument("--write", action="store_true", help="overwrite the baseline with live data")
    p.add_argument("--json-out")
    args = parser.parse_args(argv)
    if args.data_dir and "${" not in args.data_dir:
        os.environ["CLAUDE_PLUGIN_DATA"] = args.data_dir

    if args.cmd in hooks.HANDLERS:
        try:
            try:
                hook_input = json.load(sys.stdin)
            except ValueError:
                hook_input = {}
            output = hooks.HANDLERS[args.cmd](_well_formed(hook_input))
            if output:
                print(json.dumps(output))
        except Exception as exc:  # noqa: BLE001 - a hook must never break the session
            print(f"model-router {args.cmd}: {exc}", file=sys.stderr)
        return 0

    config = cfg.load_config()
    options = settings.resolve(config, recorded=True)
    if args.cmd == "policy":
        print(report.policy(config, options))
    elif args.cmd == "models":
        if args.reset:
            store.update_state(lambda state: state.pop("health", None))
            learn.reset()
            if not args.json:
                print("Learned availability and feedback cleared.\n")
        print(json.dumps(report.models_data(config, options), indent=2) if args.json
              else report.models(config, options))
    elif args.cmd == "explain":
        print(report.explain(config, options, " ".join(args.text), args.agent))
    elif args.cmd == "refresh":
        print(report.status(upstream.pending(upstream.refresh(options, force=args.force))))
    elif args.cmd == "status":
        changes = upstream.pending()
        print(json.dumps(changes, indent=2) if args.json else report.status(changes))
    elif args.cmd == "ack":
        version = upstream.ack()
        if version is None:
            print("Nothing cached yet; run refresh first.")
            return 1
        print(f"Marked Claude Code {version} and the current docs index as reviewed.")
    elif args.cmd == "usage":
        days = int(args.days) if str(args.days).isdigit() else 7
        print(json.dumps(report.usage_data(days), indent=2) if args.json else report.usage(days))
    elif args.cmd == "upstream-check":
        baseline_path = Path(args.baseline)
        live = upstream.fetch(options)
        if args.write:
            baseline = upstream.snapshot(live)
            store.save_json(baseline_path, baseline)
            print(f"Baseline written for Claude Code {baseline['claude_code_version']} "
                  f"({len(live['pages'])} docs pages).")
            return 0
        changes = upstream.diff(store.load_dict(baseline_path), live)
        if args.json_out:
            Path(args.json_out).write_text(json.dumps({**changes, "pending": upstream.has_pending(changes)},
                                                      indent=2), encoding="utf-8")
        print(report.status(changes))
    return 0
