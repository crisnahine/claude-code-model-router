# Model Router

A plugin for Claude Code that keeps each subagent's model and effort within the limits you set. It is an independent project, not made or endorsed by Anthropic.

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`.

Options, in `/config`: `routing_mode` (`auto`), `max_tier` (`none`) and `verify_gate` (`true`). Commands: `/model-router:route`, `/model-router:models` and `/model-router:usage`.

What it runs: the hooks in `hooks/hooks.json` and the mod `hooks/router.ts`, which start `sh` and Python on your machine. What it keeps: a ledger and state files in the plugin's data folder, private to your user on macOS and Linux, never a prompt, brief or description. It makes no network call of its own.

How it decides, every option and every limit are in the [main README](https://github.com/crisnahine/claude-code-model-router#readme). Apache-2.0.
