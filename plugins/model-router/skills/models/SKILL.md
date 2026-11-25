---
name: models
description: Shows which Claude models Model Router can route to here, what each last ran as, and which are capped, blocked or routed around.
argument-hint: "[reset]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models *)
---

# Models in use

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models --routing-mode='${user_config.routing_mode}' --max-tier='${user_config.max_tier}' --verify-gate='${user_config.verify_gate}' --session-check='${user_config.session_check}' $ARGUMENTS`

Show the output above. "Last ran as" is the model Claude Code reported for the most recent agent on that model. With `reset` as the argument, the first line says what was cleared, or what was left and why. `reset` is for a model marked "routed around" that should be tried again now.

If a model is routed around, explain in one line what that means: calls that would have used it go to the next stronger usable model, or the strongest usable one if nothing stronger is left, until it is tried again a day later.
