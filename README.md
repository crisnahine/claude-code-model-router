# Model Router

A plugin for Claude Code that keeps each subagent's model and effort within the limits you set.

<p align="center">
  <a href="CHANGELOG.md"><img src="docs/assets/badge-version.svg" height="28" alt="version 0.2.0"></a>
  <a href="#requirements"><img src="docs/assets/badge-claude-code.svg" height="28" alt="Claude Code 2.1.329 or later"></a>
  <a href="#requirements"><img src="docs/assets/badge-python.svg" height="28" alt="Python 3.9 or later"></a>
  <a href="CONTRIBUTING.md"><img src="docs/assets/badge-dependencies.svg" height="28" alt="dependencies: none, standard library only"></a>
  <a href="LICENSE"><img src="docs/assets/badge-license.svg" height="28" alt="license: Apache-2.0"></a>
</p>

Claude names a `model` and `effort` on each Agent call, from a short table Model Router adds at session start: Haiku at `medium` for finding and reading, Haiku at `high` for writing and fixing to a brief, and Sonnet at `high` for a design or a review. Model Router holds those choices to your limits, fills in what a call leaves out, routes around a model that fails, and runs a call marked "Redo:" one step higher.

Model Router is an independent project, not made or endorsed by Anthropic.

## Install

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

From a shell, `claude plugin install` takes the same arguments. `claude plugin update model-router@claude-code-model-router` updates it. The [guide](docs/GUIDE.md#install-update-and-uninstall) covers scopes and uninstalling.

## Requirements

Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. Without Python the hooks do nothing, and the mod only lowers a model a call names above `max_tier`.

## How a call's model is chosen

A model named on the call comes first, unless `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` pins every subagent's model. Then come a per-agent rule, the agent's own `model` (`inherit` means the parent's), `CLAUDE_CODE_SUBAGENT_MODEL`, and last a level from the agent's tools: `lookup` for one that can't edit, write or run commands, else `standard`. Unless an environment variable set it, Model Router holds the model to your cap and the usable models, raises it to a rule's `level`, and gives it that level's effort when nothing sets one. A call left on its parent's model gets no effort. The [guide](docs/GUIDE.md#who-decides-the-model) has the full order and [the levels](docs/GUIDE.md#the-ladder).

## Redo

Claude marks a redo by starting the call's `description` with "Redo:". It climbs from the latest run of the same agent and description that finished in this session in the last 30 minutes, else from what the call would run at unmarked: one effort step up while that run was below `high`, else the next stronger usable model, else one more effort step. A redo that names more than that run runs as named. Workflow stages are never redone.

## The check before a turn ends

When Claude or a subagent changed a project file with Edit, Write or NotebookEdit and no test runner, linter or type checker ran since, the `Stop` hook asks Claude once to run the work's own check. It never asks while background work runs.

## In a session

Where its mod runs and a person is at the prompt, a line under the prompt shows the state, such as `auto · mod · cap opus · 14 calls · 5 changed · 2 redos`: the routing mode, the path that routed the latest call, the cap, and this session's subagent calls, changes and redos.

When such a session starts below the setups Model Router recommends for the main session (Opus or Fable at `medium` effort or higher, Sonnet at `xhigh` or higher), it asks once, before your first prompt, which model and then which effort to run on, or to stay. A pick runs Claude Code's `/model` and `/effort`, which Claude Code keeps as your default for new sessions (`max` lasts this session only). It never asks once the conversation has a prompt in it.

## Options

In `/config`:

- `routing_mode`: `auto` (default), or `off` to leave every call as it is, with no session note, no check before a turn ends and no session check.
- `max_tier`: `none` (default), `haiku`, `sonnet`, `opus` or `fable`, the strongest model a subagent may run on.
- `verify_gate`: `true` (default) or `false`, the check before a turn ends.
- `session_check`: `ask` (default) or `off`, the question at session start.

Per-agent rules go in `~/.claude/model-router.json`, or in a project's `.claude/model-router.json`, which can only raise ([rules files](docs/GUIDE.md#rules-files)).

## Commands

- `/model-router [on | off | check on | check off]`: the status line, the session's model and effort, and how to switch. `on` and `off` switch routing for this session only; `check on` and `check off` set `session_check`. It answers with no model turn.
- `/model-router:route [task]`: which model and effort fit a task, and what a call would run on.
- `/model-router:models [reset]`: which models are in use, capped, blocked or routed around. `reset` clears what it learned about them.
- `/model-router:usage [days]`: calls per agent with levels and models, redos, tokens per model and Claude Code's cost estimate per subagent (default 7 days).

Its five agents are `model-router:scout` and `verifier` on Haiku at `medium`, `implementer` on Haiku at `high`, and `architect` and `reviewer` on Sonnet at `high`.

## What it stores

Its state is in the plugin's data folder, readable only by you on macOS and Linux. The ledger keeps what each call ran on and why, and each subagent's tokens and cost estimate, for 30 days and at most 4 MiB. It stores no prompt, brief or description, and makes no network call of its own.

## What it can't do

- It never changes the main conversation's model or effort on its own, and never changes which agent a call runs.
- It leaves forks alone, and an agent whose definition it can't find unless the call or your rule names a model.
- A cloned repository's `.claude/model-router.json` can raise its agents up to your `max_tier`.
- It retries no failed call. After two model errors on a model, or two runs on another model, it routes around that model for a day.
- Each hook starts Python ([performance](docs/PERFORMANCE.md)).
- Without its mod, a `PreToolUse` hook routes Agent calls, and there is no workflow routing, token count, cost estimate, status line, `/model-router` or session check.

## Benchmark

[docs/BENCHMARKS.md](docs/BENCHMARKS.md) compares Claude Code with and without Model Router on fixed tasks run headless from `bench/`: how many runs pass each task's check, and the cost per solved task.

## Documentation

[Guide](docs/GUIDE.md), [questions](docs/FAQ.md), [architecture](docs/ARCHITECTURE.md), [security](docs/SECURITY.md), [performance](docs/PERFORMANCE.md), [Claude Code docs](docs/CLAUDE_CODE_DOCS.md), [changelog](CHANGELOG.md), [contributing](CONTRIBUTING.md), and [every page](docs/README.md). Bugs: [GitHub issues](https://github.com/crisnahine/claude-code-model-router/issues).

## License

Apache-2.0. See [LICENSE](LICENSE). Copyright 2026 Cris Nahine.
