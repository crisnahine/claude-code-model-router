---
name: refresh
description: Checks the live Claude Code changelog, the docs pages Model Router is built on, the model aliases and the model status page against compat/baseline.json, and reports what they mean for the plugin.
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_PROJECT_DIR}/tools/upstream_check.py" --baseline "${CLAUDE_PROJECT_DIR}/compat/baseline.json")
  - Bash(date +%F)
---

# Review upstream Claude Code changes

## What changed since the last review

!`python3 "${CLAUDE_PROJECT_DIR}/tools/upstream_check.py" --baseline "${CLAUDE_PROJECT_DIR}/compat/baseline.json"`

## Steps

1. If the output above ends with "Nothing to review.", tell the user nothing upstream affects the plugin and stop.
2. Otherwise send the list above to `model-router:docs-researcher`, starting the brief with today's date: !`date +%F`. Ask it to read each new release's entry in the changelog and each listed page, and report items that affect:
   - model aliases or new models (the registry in `plugins/model-router/config/models.json` lists the models the plugin routes to)
   - agent or skill frontmatter fields
   - hook events, hook input or output fields
   - `plugin.json`, marketplace, evals or workflows
   - anything deprecated or removed
3. Present a short impact report: each item, what it affects, and the recommended action. Mark breaking changes first.
4. Offer to apply the recommended changes on a new branch: update the files, run the gates in `CONTRIBUTING.md`, mark the changes as reviewed with `python3 tools/upstream_check.py --baseline compat/baseline.json --write`, and add a `CHANGELOG.md` entry. Do not push without the user's go-ahead.
