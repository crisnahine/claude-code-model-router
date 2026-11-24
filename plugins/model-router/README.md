# Model Router

Model Router (`model-router`) is a Claude Code plugin that sets the model and reasoning effort of every agent Claude Code starts, choosing the cheapest Claude model that should do the job and moving a call one level up when its work comes back for a redo.

Install it in Claude Code:

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

In a session this opens the plugin in `/plugin`, where you pick a scope.

Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. The eight options in `/config` start at their defaults, so nothing has to be set.

Commands: `/model-router:route`, `/model-router:usage`, `/model-router:models`, `/model-router:refresh` and `/model-router:ship`. Type `/model-router:ship` in full: where Claude Code has its own `/ship`, the bare `/ship` runs that command, which can commit, push and open a pull request with no confirmation prompt.

A [22-second video](https://crisnahine.github.io/claude-code-model-router/assets/video.mp4) shows four agent calls being routed and one climbing a level after a redo. How it works, options, benchmarks, limits, privacy and updates are in the [main README](https://github.com/crisnahine/claude-code-model-router#readme). Apache-2.0; not made or endorsed by Anthropic.
