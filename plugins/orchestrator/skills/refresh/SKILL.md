---
name: refresh
description: Check the live Claude Code changelog and documentation for releases, new features, model updates and breaking changes since the last review, and report what they mean for this plugin and the user's setup.
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.py *)
---

# Review upstream Claude Code changes

## What changed since the last review

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.py --data-dir "${CLAUDE_PLUGIN_DATA}" refresh --force`

## Steps

1. If the output above shows no new releases and no high-impact pages, tell the user everything is current and stop.
2. Otherwise send the list above to `orchestrator:docs-researcher`. Ask it to read each new release's changelog entry and each high-impact docs page, and report items that affect:
   - model aliases or new models (this plugin routes by `haiku`, `sonnet`, `opus`, `fable`)
   - agent or skill frontmatter fields
   - hook events, hook input or output fields
   - `plugin.json`, marketplace, evals or workflows
   - anything deprecated or removed
3. Present a short impact report: each item, what it affects, and the recommended action. Mark breaking changes first.
4. Ask the user whether to mark these changes as reviewed. When they agree, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.py --data-dir "${CLAUDE_PLUGIN_DATA}" ack`.

## If the current project is the orchestrator plugin's own repository

When `plugins/orchestrator/.claude-plugin/plugin.json` exists in the working directory, offer to apply the recommended changes on a new branch: update the files, run `python3 -m unittest discover -s tests` and `claude plugin validate --strict plugins/orchestrator`, update `compat/baseline.json` with `python3 plugins/orchestrator/scripts/orchestrator.py upstream-check --baseline compat/baseline.json --write`, and add a `CHANGELOG.md` entry. Do not push without the user's go-ahead.
