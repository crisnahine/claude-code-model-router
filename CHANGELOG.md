<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="docs/ROADMAP.md">Roadmap</a> &nbsp;&middot;&nbsp; Next: <a href="CONTRIBUTING.md">Contributing</a></p>
<!-- End of page head. -->

# Changelog

## 0.2.0 - 2026-11-25

Model Router manages the Claude model and effort that each subagent, teammate and workflow agent runs on.

- Claude names the `model` and `effort` on each Agent call, from a table in the session-start note: Haiku at `medium` for finding, reading and listing, Haiku at `high` for writing and fixing code, figures and documents to a brief, and Sonnet at `high` for a judgment call with no check to run (a design or a review). Opus and Fable have no row: they are for the session itself, and a subagent runs on one when a redo climbs there, a rule sets its level or the call names it.
- The plugin keeps each call within your cap and the models usable here, fills in the effort when a call names only a model, gives a call that names no model a level from what its agent can do (`lookup` when it can't edit, write or run commands, else `standard`), and raises an agent's calls to the `level` a rule in `model-router.json` sets.
- A call whose `description` starts with "Redo:" runs higher than the run it redoes: more effort first, then a stronger model.
- When Claude edited a project file and no check ran after it, the Stop check asks Claude once to run the work's own check before it finishes.
- In a session with a person at the prompt, the mod shows a status line under the prompt (mode, path, cap, this session's calls and how many were changed or redone), and `/model-router` prints the state or switches routing on or off for this session only, with no model turn.
- When a new interactive session starts below the recommended main-session setups (Opus or Fable at `medium` effort or higher, Sonnet at `xhigh` or higher), the mod asks once which model, then which effort, and runs Claude Code's `/model` and `/effort` for a pick, which Claude Code also keeps as the default for new sessions. Stay can stop the question for good.
- Options, in `/config`: `routing_mode` (`auto` or `off`), `max_tier` (the strongest model a subagent may run on, `none` for no cap), `verify_gate` (the Stop check, on by default) and `session_check` (`ask` or `off`).
- Commands: `/model-router` (state, `on`, `off`, `check on`, `check off`), `/model-router:route` says which model and effort fit a task, `/model-router:models` shows the models usable here (`reset` clears learned availability), and `/model-router:usage` shows recent routing, tokens per model and Claude Code's cost estimate per subagent.
- Agents: `scout` and `verifier` on Haiku at `medium`, `implementer` on Haiku at `high`, and `architect` and `reviewer` on Sonnet at `high`.
- Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python`. The plugin makes no network call.
- If you installed from `main` on 2026-11-22 or 2026-11-23, delete `~/.claude/plugins/cache/claude-code-model-router/model-router/0.2.0` first. That older copy has the same version number, and Claude Code keeps running it.

## 0.1.2 - 2026-11-24

The second release, which 0.2.0 replaces.

## 0.1.0 - 2026-11-23

The first release.
