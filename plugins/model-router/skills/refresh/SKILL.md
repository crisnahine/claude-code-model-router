---
name: refresh
description: Checks the live Claude Code changelog and documentation for releases, new features, model updates and breaking changes since the last review, and reports what they mean for this plugin and the user's setup.
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" *)
---

# Review upstream Claude Code changes

## What changed since the last review

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" refresh --force`

## Steps

1. If the output above shows nothing after the "No new releases." line, tell the user everything is current and stop.
2. Otherwise send the list above to `model-router:docs-researcher`. Ask it to read each new release's changelog entry and each high-impact docs page, and report items that affect:
   - model aliases or new models (the registry in `config/models.json` lists the models this plugin routes to)
   - agent or skill frontmatter fields
   - hook events, hook input or output fields
   - `plugin.json`, marketplace, evals or workflows
   - anything deprecated or removed
3. Present a short impact report: each item, what it affects, and the recommended action. Mark breaking changes first.
4. Ask the user whether to mark these changes as reviewed. When they agree, run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" ack`.

## If the current project is the Model Router plugin's own repository

When `plugins/model-router/.claude-plugin/plugin.json` exists in the working directory, offer to apply the recommended changes on a new branch: update the files, run `python3 -m unittest discover -s tests` and `claude plugin validate --strict plugins/model-router`, update `compat/baseline.json` with `python3 plugins/model-router/scripts/model-router.py upstream-check --baseline compat/baseline.json --write`, and add a `CHANGELOG.md` entry. Do not push without the user's go-ahead.
