# Model Router

A plugin for Claude Code that keeps each subagent's model and effort within the limits you set.

<p align="center">
  <a href="CHANGELOG.md"><img src="docs/assets/badge-version.svg" height="28" alt="version 0.2.0"></a>
  <a href="#requirements"><img src="docs/assets/badge-claude-code.svg" height="28" alt="Claude Code 2.1.329 or later"></a>
  <a href="#requirements"><img src="docs/assets/badge-python.svg" height="28" alt="Python 3.9 or later"></a>
  <a href="CONTRIBUTING.md"><img src="docs/assets/badge-dependencies.svg" height="28" alt="dependencies: none, standard library only"></a>
  <a href="LICENSE"><img src="docs/assets/badge-license.svg" height="28" alt="license: Apache-2.0"></a>
</p>

Model Router, a plugin for Claude Code, adds a session-start note that tells Claude which model and effort fit which work and asks it to name both on every Agent call. It keeps what Claude names within your cap and the models usable here, fills in the effort when only a model is named, gives a call that names no model a level from what its agent can do, and runs a call marked as a redo with more effort or on a stronger model.

Model Router is an independent project, not made or endorsed by Anthropic.

## Install

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

From a shell: `claude plugin install` with the same arguments. `claude plugin update model-router@claude-code-model-router` updates it.

## Requirements

Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. Without Python the hooks do nothing.

## Options

In `/config`:

- `routing_mode`: `auto` (default), or `off` to leave every call as it is, with no notes, no check and no session check.
- `max_tier`: the strongest model subagents may run on, except one an environment variable sets: `none` (default), `haiku`, `sonnet`, `opus` or `fable`.
- `verify_gate`: `true` (default) turns on the check before a turn ends.
- `session_check`: `ask` (default) offers a stronger setup when a new session starts below the recommended ones, or `off`.

Per-agent rules go in `~/.claude/model-router.json` or a project's `.claude/model-router.json`, which can only raise ([keys](docs/GUIDE.md)).

## Who decides a call's model

The first that applies:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` (the router sets effort only)
2. `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`, for a workflow stage whose call names no model (effort only)
3. The model named on the call
4. A per-agent rule in `model-router.json`
5. The agent's own `model` field, where `inherit` means the parent's model
6. `CLAUDE_CODE_SUBAGENT_MODEL`
7. The agent's tools: `lookup` for an agent that can't edit, write or run commands (its `tools` or `disallowedTools` leave out Edit, Write, NotebookEdit and Bash, as in Explore and Plan), `standard` for any other. A teammate starts no lower than `standard`.

A model from 3, 4, 5 or 7 is held to `max_tier`, `availableModels`, `deniedModels` and model health: one ruled out moves to the next stronger usable model, else the strongest usable. A rule's `level` raises a call below it, the agent's tools' level included. To keep an agent on its parent's model, set `route: false` for it or `model: inherit` in its own file.

Levels: lookup (Haiku, `medium`), standard (Haiku, `high`), complex (Sonnet, `high`), hardest (Opus, `high`), frontier (Fable, `high`). With no effort set, a model from 1 to 5 or 7, or one Model Router moved the call to, gets an effort from its level. A call left on 6, or on the parent's model through `inherit`, gets none.

## Redo

Claude marks a redo by starting the call's `description` with "Redo:". It climbs from the latest finished run of the same agent and description in the session's last 30 minutes, else from what the call would run at unmarked: one effort step up while that run was below `high`, else the next stronger usable model, else one more effort step. A redo that names a stronger model, or more effort on the same one, runs as named. An effort held by an environment variable or `maxEffortLevel` sends it to the next model. Workflow stages are never redone.

## The check before a turn ends

When Claude changed a project file with Edit, Write or NotebookEdit and no check command ran since, the `Stop` hook asks Claude once for the work's own check, such as tests for code or a reread of a document. It runs nothing itself and waits out background work.

## In a session

With its mod loaded and a person at the prompt, Model Router shows one line under the prompt, after the plugin's name, such as `auto · mod · cap opus · 14 calls · 5 changed · 2 redos`: routing on (`auto`) or `off`, the path that routes calls, the cap when one is set, this session's subagent calls, and how many of them the router changed and how many were redos, each shown once there is one. `(this session)` follows the mode while `/model-router` overrides the saved option. The line updates after each spawn and each turn.

`/model-router` prints the same state with no model turn. `/model-router off` and `/model-router on` switch routing for this session only, from the next spawn; `routing_mode` in `/config` keeps a choice. `/model-router check off` and `check on` set `session_check`.

### The session check

The session that plans and delegates works best on a strong setup: Opus or Fable at `medium` effort or higher, or Sonnet at `xhigh` or higher. When a new session starts below these, Model Router asks once, before your first prompt:

1. Which model: each recommended model usable here, then `Stay on <model> at <effort>`. The first is marked `(Recommended)` and the strongest `(the strongest model)`.
2. Which effort, from that model's lowest recommended effort up to `max`. A pick runs Claude Code's `/model` when the model changes and `/effort` when the effort does, and Claude Code keeps both as your default for new sessions (`max` lasts this session only).

After Stay, it asks whether to ask again next session or never; `/model-router check on` brings it back.

A dismissal or typed text changes nothing. It never asks in `claude -p` or the Agent SDK, once the conversation has a prompt in it, with routing off, or with `session_check` off, and at most once per session. Before the first request, the effort is an estimate from `CLAUDE_CODE_EFFORT_LEVEL`, a `--effort` flag at launch, your settings files and the model's default; an organization's default effort isn't visible to it.

## Commands

- `/model-router [on | off | check on | check off]`: the state above, or a switch. Only with the mod.
- `/model-router:route [task]`: which model and effort fit a task, and what a call would run on.
- `/model-router:models [reset]`: which models are in use, capped, blocked or routed around. `reset` clears model health.
- `/model-router:usage [days]`: agents, levels, models, tokens, redos and Claude Code's cost estimate per subagent (default 7 days).

It ships five agents, under `model-router:`: `scout` and `verifier` on Haiku at `medium`, `implementer` on Haiku at `high`, and `architect` and `reviewer` on Sonnet at `high`.

## What it stores

Its files stay in the plugin's data folder, private to your user on macOS and Linux. The ledger keeps each call's agent, level, model, effort and reason, and each subagent's tokens and cost estimate, for up to 30 days and 4 MB. It stores no prompt, brief or description, and makes no network call of its own.

## What it can't do

- It never changes the main conversation's model or effort by itself: only a setup you pick in the session check does, through `/model` and `/effort`. It never changes which agent a call runs.
- It leaves forks alone, and an agent whose definition file it can't find, unless the call or your rule names a model.
- A cloned repository's `.claude/model-router.json` can raise its agents up to your `max_tier`.
- It retries nothing. After two model errors on a model, it routes around it for a day.
- Each hook starts Python ([performance](docs/PERFORMANCE.md)).
- Workflow stages, token counts, cost estimates, the status line, `/model-router` and the session check need its mod; otherwise a `PreToolUse` hook routes Agent calls, the state is in `/config` and `/model-router:usage`, and nothing asks about the session.

## Benchmark

The benchmark in `bench/` runs fixed tasks headless with and without the plugin, and compares tasks solved and cost per solved task. Results appear in [docs/BENCHMARKS.md](docs/BENCHMARKS.md) as rounds are recorded.

## Documentation

[Guide](docs/GUIDE.md), [questions](docs/FAQ.md), [architecture](docs/ARCHITECTURE.md), [security](docs/SECURITY.md), [performance](docs/PERFORMANCE.md), [Claude Code docs](docs/CLAUDE_CODE_DOCS.md), [changelog](CHANGELOG.md), [contributing](CONTRIBUTING.md), and [every page](docs/README.md). Bugs: [GitHub issues](https://github.com/crisnahine/claude-code-model-router/issues).

## License

Apache-2.0. See [LICENSE](LICENSE). Copyright 2026 Cris Nahine.
