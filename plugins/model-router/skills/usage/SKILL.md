---
name: usage
description: Shows how Model Router routed work recently: which agents ran, at what level, on which models, how many tokens each model used, and what it cost at list price.
argument-hint: "[days, default 7] [--json]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" usage *)
---

# Model Router usage

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" usage $ARGUMENTS`

If the arguments include `--json`, the output above is one JSON object in the `model-router.usage/1` schema: show it unchanged in a `json` code block and add nothing.

Otherwise show the table above to the user. Then add at most three observations, for example an agent that mostly gets lookups but runs on a strong model because it names one, a level that is often redone on a stronger model, background agents whose model wasn't reported, or where most of the spend went. The dollars are list prices from the registry; for the bill, point them to `/usage` and `/cost`.
