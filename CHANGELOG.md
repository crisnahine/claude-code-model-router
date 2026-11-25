<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="docs/ROADMAP.md">Roadmap</a> &nbsp;&middot;&nbsp; Next: <a href="CONTRIBUTING.md">Contributing</a></p>
<!-- End of page head. -->

# Changelog

## 0.2.0 - 2026-11-25

Model Router manages the Claude model and effort that each subagent, teammate and workflow agent runs on.

- Claude names the `model` and `effort` on each Agent call, from a table in the session-start note: Haiku at `medium` for finding, reading and listing, Sonnet at `medium` for everyday work to a clear brief, Opus at `high` for work that is hard to check, high-stakes, ambiguous or broad, and Fable at `high` for open-ended research.
- The plugin keeps each call within your cap and the models usable here, fills in the effort when a call names only a model, and raises an agent's calls to the `level` a rule in `model-router.json` sets.
- A call whose `description` starts with "Redo:" runs higher than the run it redoes: more effort first, then a stronger model.
- When Claude edited a project file and no check ran after it, the Stop check asks Claude once to run the work's own check before it finishes.
- Options, in `/config`: `routing_mode` (`auto` or `off`), `max_tier` (the strongest model a subagent may run on, `none` for no cap) and `verify_gate` (the Stop check, on by default).
- Commands: `/model-router:route` says which model and effort fit a task, `/model-router:models` shows the models usable here (`reset` clears learned availability), and `/model-router:usage` shows recent routing, tokens per model and Claude Code's cost estimate per subagent.
- Agents: `architect` and `reviewer` on Opus at `high`, `scout` and `verifier` on Haiku at `medium`, and `implementer` on the model the call names.
- Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python`. The plugin makes no network call.
- If you installed from `main` on 2026-11-22 or 2026-11-23, delete `~/.claude/plugins/cache/claude-code-model-router/model-router/0.2.0` first. That older copy has the same version number, and Claude Code keeps running it.

## 0.1.2 - 2026-11-24

The second release, which 0.2.0 replaces.

## 0.1.0 - 2026-11-23

The first release.
