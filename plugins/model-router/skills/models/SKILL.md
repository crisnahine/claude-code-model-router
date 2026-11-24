---
name: models
description: Shows which Claude models Model Router can route to in this setup, what each really runs as, which are capped, blocked or unavailable, what the feedback loop has learned, and any new model in the docs that isn't routed to yet.
argument-hint: "[reset] [--json]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models *)
---

# Models in use

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models`

Arguments: $ARGUMENTS

If the arguments include `--json`, run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models --json` (with `--reset` too if the arguments include `reset`). It prints one JSON object in the `model-router.models/1` schema: show it unchanged in a `json` code block and add nothing else.

Otherwise show the table above. "Last ran as" is the model Claude Code reported for the most recent agent on that model.

If the arguments are `reset` without `--json`, run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models --reset` and show its first lines, which say what was cleared and what was left and why, then the new table. Do this when a model marked "routed around" should be tried again now, or to undo a level the feedback loop raised.

If a model is routed around, explain in one line what that means: calls that would have used it run on the next model down until it is tried again, a day later at first and up to a week if it keeps failing. If a level shows as raised, explain that work at that level kept being redone on a stronger model. If the docs list an alias that isn't in the registry, say that `/model-router:refresh` reviews it, and that adding it is one entry under `models` in `~/.claude/model-router.json` once the installed Claude Code accepts that alias on agent calls.
