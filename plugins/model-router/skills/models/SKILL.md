---
name: models
description: Shows which Claude models Model Router can route to in this setup, what each really runs as, and which are capped, blocked or unavailable.
argument-hint: "[reset]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models *)
---

# Models in use

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" models --routing-mode="${user_config.routing_mode}" --priority="${user_config.priority}" --max-tier="${user_config.max_tier}" --agent-pins="${user_config.agent_pins}" --verify-gate="${user_config.verify_gate}" $ARGUMENTS`

Show the output above. "Last ran as" is the model Claude Code reported for the most recent agent on that model. With `reset` as the argument, the first line says what was cleared, or what was left and why: `reset` is for a model marked "routed around" that should be tried again now.

If a model is routed around, explain in one line what that means: calls that would have used it go to the next usable model that is strong enough, or the strongest one left if none is (a model set by name, on the call, a pin or a rule, goes to the nearest usable one below it, or the weakest usable if none is), until it is tried again, a day later at first and up to a week if it keeps failing.
