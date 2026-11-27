<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="docs/ROADMAP.md">Roadmap</a> &nbsp;&middot;&nbsp; Next: <a href="CONTRIBUTING.md">Contributing</a></p>
<!-- End of page head. -->

# Changelog

## 0.2.1 - 2026-11-27

This release follows Claude Code 2.1.332 and cleans up the code. Options and commands are the same as in 0.2.0.

- On Google Cloud's Agent Platform `sonnet` goes out as the alias, and on Microsoft Foundry `opus` and `sonnet` do: Claude Code 2.1.332 runs the newest model for them. On 2.1.329 to 2.1.331 those three aliases run the provider's older model.
- Under `availableModelsMatch: exact`, an `availableModels` entry such as `claude-opus-4-5` also permits its dated model ID, as in Claude Code.
- A usage report whose cost is too large a number to store is recorded with no cost. Before, the usage hook stopped with an error on it.
- Dead code, repeated helpers and repeated tests are gone from the runtime, the benchmark harness and the docs tooling.

## 0.2.0 - 2026-11-26

Model Router manages the Claude model and effort that each subagent, teammate and workflow agent runs on.

- Claude names the `model` and `effort` on each Agent call, from a table in the session-start note: Haiku at `medium` for finding, reading and listing, Haiku at `high` for writing and fixing code, figures and documents to a brief, and Sonnet at `high` for a judgment call with no check to run (a design or a review). Opus and Fable have no row.
- The plugin keeps each call within your cap and the models usable here, fills in the effort when a call names only a model, gives a call that nothing names a model for a level from what its agent can do (`lookup` when it can't edit, write or run commands, else `standard`), and raises an agent's calls to the `level` a rule in `model-router.json` sets.
- After two model errors on a model, or two runs on another model, calls route around that model for a day.
- On a provider whose alias runs an older model, the mod sends the family's newest model ID.
- A call whose `description` starts with "Redo:" runs above the run it redoes: one effort step up while that run was below `high`, else a stronger model, else one more effort step.
- When Claude edited a project file with Edit, Write or NotebookEdit and no check ran after it, the Stop check asks Claude once to run the work's own check before it finishes.
- In a session with a person at the prompt, the mod shows a status line under the prompt (mode, path, cap, this session's calls and how many were changed or redone), and `/model-router` prints the state or switches routing on or off for this session only, with no model turn.
- When a new interactive session starts below the recommended main-session setups (Opus or Fable at `medium` effort or higher, Sonnet at `xhigh` or higher), the mod asks once which model, then which effort, and runs Claude Code's `/model` and `/effort` for a pick, which Claude Code also keeps as the default for new sessions (`max` lasts the session only). After Stay, "Don't ask again" sets `session_check` to `off`.
- Options, in `/config`: `routing_mode` (`auto` or `off`), `max_tier` (the strongest model a subagent may run on, `none` for no cap), `verify_gate` (the Stop check, on by default) and `session_check` (`ask` or `off`).
- Commands: `/model-router` (state, `on`, `off`, `check on`, `check off`), `/model-router:route` says which model and effort fit a task, `/model-router:models` shows each model's state here (`reset` clears what it learned), and `/model-router:usage` shows recent routing, tokens per model and Claude Code's cost estimate per subagent.
- Agents: `scout` and `verifier` on Haiku at `medium`, `implementer` on Haiku at `high`, and `architect` and `reviewer` on Sonnet at `high`.
- Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python`. The plugin makes no network call.
- Measured on an Opus session, 80 runs per setup: Model Router solved 80 of 80 at $0.183 per solved task, Claude Code's defaults 80 of 80 at $0.361, and every subagent forced onto Haiku 78 of 80 at $0.139. [docs/BENCHMARKS.md](docs/BENCHMARKS.md) has the tables, the method and the limits.

## 0.1.2 - 2026-11-24

The second release, which 0.2.0 replaces.

## 0.1.0 - 2026-11-23

The first release.
