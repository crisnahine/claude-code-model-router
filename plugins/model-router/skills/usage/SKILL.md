---
name: usage
description: "Shows how Model Router routed work recently: which agents ran, at what level, on which models, how many tokens each model used, and Claude Code's cost estimate by agent."
argument-hint: "[days, default 7]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" usage *)
---

# Model Router usage

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" usage $ARGUMENTS`

Show the output above. Then add at most three observations, for example an agent that runs on Opus or Fable for most of its calls, a level that is often redone, background agents whose model wasn't reported, or where most of the spend went. Spend is recorded only where the plugin's mod runs.
