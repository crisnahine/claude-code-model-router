---
name: usage
description: "Shows how Model Router routed work recently: which agents ran, at what level, on which models, how many tokens each model used, and Claude Code's cost estimate for each subagent."
argument-hint: "[days, default 7]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" usage *)
---

# Model Router usage

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" usage $ARGUMENTS`

Show the table above to the user. Then add at most three observations, for example an agent that mostly gets lookups but runs on a strong model because it names one, a level that is often redone on a stronger model, background agents whose model wasn't reported, or where most of the spend went. The dollars are Claude Code's own estimate for each subagent, not the bill, and on a subscription usage inside the plan isn't billed in dollars. For the session's own cost and plan usage, point them to `/usage`.
