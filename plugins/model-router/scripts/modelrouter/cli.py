"""Command line: hook commands read hook JSON on stdin; the rest serve skills and scripts.

  hooks      session-start, user-prompt, pre-agent, post-agent, agent-failed, agent-stopped,
             post-edit, stop, model-switch, and mod-start, mod-spawn, mod-usage for hooks/router.ts
  policy     the routing policy as it stands for this user and project
  models     what each registry model really runs as, and what is unavailable
             (reset forgets learned availability; --json prints model-router.models/3)
  explain    what an agent call would run on and why: explain [--agent NAME] [--model M] [--effort E] [TEXT]
  usage      the ledger by agent and model: usage [DAYS] [--json for model-router.usage/2]

policy, models and explain take the plugin's options as flags, such as --max-tier=sonnet:
skills pass them as ${user_config.KEY}, since the Bash tool doesn't carry CLAUDE_PLUGIN_OPTION_<KEY>.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import health, hooks, report, settings, store


# Hook fields the handlers use as text: as file keys, paths, or things to match against.
_TEXT_FIELDS = ("session_id", "cwd", "transcript_path", "prompt", "agent_id", "agent_type",
                "model", "to_model", "error")


def _well_formed(hook_input: object) -> dict:
    """Hook input with anything of the wrong type dropped, so a handler sees a field as
    text or not at all."""
    if not isinstance(hook_input, dict):
        return {}
    return {key: value for key, value in hook_input.items()
            if key not in _TEXT_FIELDS or isinstance(value, str)}


_NEWER = "left as is: {} was written by a newer Model Router release."
_BUSY = "left as is: another Model Router process held {}. Run it again."


def _reset_health() -> str:
    removed = False
    try:
        removed, status = health.forget()
    except OSError:
        status = "unreadable"
    if status == "saved":
        return "Learned availability cleared." if removed else "No learned availability to clear."
    if status == "newer":
        return "Learned availability " + _NEWER.format("state.json")
    if status == "busy":
        return "Learned availability " + _BUSY.format("state.json")
    return "Learned availability left as is: state.json could not be read or written."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="model-router")
    parser.add_argument("--data-dir", help="plugin data directory; skills pass ${CLAUDE_PLUGIN_DATA}")
    options = argparse.ArgumentParser(add_help=False)
    for key in settings.OPTIONS:
        options.add_argument("--" + key.replace("_", "-"), dest=key)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in hooks.HANDLERS:
        sub.add_parser(name)
    sub.add_parser("policy", parents=[options])
    p = sub.add_parser("models", parents=[options])
    p.add_argument("action", nargs="*")  # the skill passes whatever the user typed; only reset counts
    p.add_argument("--reset", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("explain", parents=[options])
    p.add_argument("--agent")
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("text", nargs="*")
    p = sub.add_parser("usage")
    p.add_argument("days", nargs="?", default="7")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.data_dir and "${" not in args.data_dir:
        os.environ["CLAUDE_PLUGIN_DATA"] = args.data_dir
    for key in settings.OPTIONS:
        value = getattr(args, key, None)
        if value and "${" not in value:  # an unsubstituted ${user_config.KEY} leaves the option at its default
            os.environ[f"CLAUDE_PLUGIN_OPTION_{key.upper()}"] = value

    if args.cmd in hooks.HANDLERS:
        try:
            try:
                # Bytes: json detects UTF-8 itself, where the text stream would decode in the locale's code page.
                hook_input = json.loads(sys.stdin.buffer.read())
            except ValueError:
                hook_input = {}
            hook_input = _well_formed(hook_input)
            if isinstance(hook_input.get("session_id"), str):
                store.owner = hook_input["session_id"]
            output = hooks.HANDLERS[args.cmd](hook_input)
            if output:
                print(json.dumps(output))
        except Exception as exc:  # noqa: BLE001 - a hook must never break the session
            print(f"model-router {args.cmd}: {exc}", file=sys.stderr)
        return 0

    snap = settings.Snapshot(None)
    if args.cmd == "policy":
        print(report.policy(snap))
    elif args.cmd == "models":
        if args.reset or "reset" in args.action:
            cleared = f"{_reset_health()}\n"
            if not args.json:
                print(cleared)
        print(json.dumps(report.models_data(snap), indent=2) if args.json
              else report.models(snap))
    elif args.cmd == "explain":
        print(report.explain(snap, " ".join(args.text), args.agent, args.model, args.effort))
    elif args.cmd == "usage":
        days = int(args.days) if str(args.days).isdigit() and int(args.days) > 0 else 7  # ledger.read(0) is everything
        print(json.dumps(report.usage_data(days), indent=2) if args.json else report.usage(days))
    return 0
