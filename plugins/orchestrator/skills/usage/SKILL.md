---
name: usage
description: Show how the orchestrator routed work recently, which agents ran, on which models, and how often the tier cap refused a spawn.
argument-hint: "[days, default 7]"
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.sh" *)
---

# Orchestrator usage

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" usage $ARGUMENTS`

Show the table above to the user. Then add at most three observations, for example a high-tier agent used for work a cheaper one handles, a cap that refuses often, or background agents whose model wasn't reported. For token and cost totals, point them to `/usage` and `/cost`.
