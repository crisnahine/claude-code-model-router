# Model Router

Model Router (`model-router`) is a Claude Code plugin that sets the model and reasoning effort of every agent Claude Code starts, choosing the cheapest Claude model that should do the job and moving a call one level up when its work comes back for a redo.

Install it in Claude Code:

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later on your `PATH`. There is nothing to configure.

Commands: `/model-router:route`, `/model-router:usage`, `/model-router:models`, `/model-router:refresh` and `/model-router:ship`.

Everything else (how it works, options, measured results, limits, privacy, update and uninstall) is in the [main README](https://github.com/crisnahine/claude-code-model-router#readme). Apache-2.0; not made or endorsed by Anthropic.
