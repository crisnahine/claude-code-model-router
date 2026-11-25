# Model Router

Model Router (`model-router`) is a Claude Code plugin that keeps the model and reasoning effort Claude names for every agent Claude Code starts inside your limits, fills in the effort when only a model is named, and runs a call Claude marks as a redo with more effort or on a stronger model.

Install it in Claude Code:

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

In a session this opens the plugin in `/plugin`, where you pick a scope.

Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. The three options in `/config` start at their defaults, so nothing has to be set.

Commands: `/model-router:route`, `/model-router:usage` and `/model-router:models`.

How it works, options, benchmarks, limits, privacy and updates, including what changes when you come from 0.1.0 or 0.1.2, are in the [main README](https://github.com/crisnahine/claude-code-model-router#readme). Apache-2.0; not made or endorsed by Anthropic.

What it runs and keeps: the hooks in `hooks/hooks.json` and a mod, `hooks/router.ts`, which start `sh` and Python on your machine. It keeps its state and a ledger in the plugin's data folder, which on macOS and Linux it sets so only your user can read it (mode 0700, and 0600 on the state and ledger files); on Windows the folder keeps the access it inherits. The ledger holds each agent call's level and model, never its brief or description, and each subagent's token counts and Claude Code's cost estimate, for 30 days and at most 4 MB. Rows that 0.1.0 and 0.1.2 wrote also hold up to 120 characters of each call's description until they age out; delete `ledger.jsonl` in the data folder to drop them now. It makes no network call and sends no prompt or code anywhere ([Privacy and security](https://github.com/crisnahine/claude-code-model-router#privacy-and-security) in the main README). Set `routing_mode` to `off` in `/config` to pause it.
