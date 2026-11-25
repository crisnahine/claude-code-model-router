<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Next: <a href="FAQ.md">Questions</a></p>
<!-- End of page head. -->

# Guide

How to install, use and configure Model Router 0.2.0.

## Install, update and uninstall

It needs Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`.

- Install: `/plugin install model-router --marketplace crisnahine/claude-code-model-router`, then pick a scope. From a shell, `claude plugin install` takes the same arguments and `--scope`.
- Update: `claude plugin update model-router@claude-code-model-router`, or **Update now** in `/plugin`. Auto-update is off by default; turn it on in the **Marketplaces** tab.
- Disable or uninstall: `claude plugin disable` or `uninstall`, with `model-router@claude-code-model-router`. Uninstalling the last install deletes the plugin's data folder unless you pass `--keep-data`.

## Commands

- `/model-router:route [task]` shows the mode, cap, ladder and role agents, which Claude uses to say what model and effort fit the task, or to delegate it.
- `/model-router:models [reset]` shows, per model, what it last ran as and whether it is working, unused, above your cap, not permitted or routed around, and what your rules files had wrong. `reset` clears the model health record first.
- `/model-router:usage [days]` shows, over `days` (default 7), calls per agent with levels and models, redos, tokens per model, model errors and, where the mod runs, Claude Code's cost estimate, which isn't your bill.

`models` and `usage` take `--json`.

## Configuration

### Options

Set in `/config`:

- `routing_mode`: `auto` (default) or `off`. `off` changes no call and adds no notes or checks, but still keeps records and tells Claude of a model error or a mid-run model swap.
- `max_tier`: `none` (default), `haiku`, `sonnet`, `opus` or `fable`, the strongest model a subagent may run on. A stronger one is lowered, except an environment pin or a fork.
- `verify_gate`: `true` (default) or `false`, the [check before a turn ends](#the-check-before-a-turn-ends).

### Rules files

Your `~/.claude/model-router.json` (in `$CLAUDE_CONFIG_DIR` when set) applies everywhere and is trusted in full. A project's `.claude/model-router.json`, the nearest one up to the repository root, only raises, so a cloned repository can't make its subagents run weaker. Both are JSON, as in `{"agents": {"my-agent": {"model": "opus"}}}`.

Agents are keyed by the name a call uses. Levels are in [the ladder](#the-ladder); efforts are `low`, `medium`, `high`, `xhigh` and `max`.

- `agents.<name>.model`: yours sets it. A project's applies only when not weaker than the call's model without it.
- `agents.<name>.level`: a floor in either file; a call below it runs on the level's model, unless the environment pins it.
- `agents.<name>.route`: `false` in yours leaves the agent alone. A project's is ignored.
- `levels.<level>.needs` (the weakest model's strength) and `.effort`: yours sets them; a project's only raises them.
- `models.<name>.strength` and `.cost`: yours adds or re-ranks a model, named by an alias the Agent tool accepts. A project's adds only new names, used only when named, never under `max_tier`.

Where your rule sets an agent's `level` or `model`, a project's rule for it is ignored. `routing_mode`, wrong types and other keys are ignored; `/model-router:models` lists what was dropped or held back. `max_tier` caps only the model; `maxEffortLevel` in your settings bounds effort.

## Who decides the model

![Who decides the model, strongest first.](assets/precedence.svg)

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` runs every subagent on `CLAUDE_CODE_SUBAGENT_MODEL`, else the conversation's model. The router sets only effort, and none on a call that names a model.
2. A model named on the call, by Claude, a skill or a workflow script. A prompt asking for one ("use opus") gets Claude a note to pass it.
3. A per-agent rule: yours, or a project's when not weaker.
4. The agent's own `model` field.
5. `CLAUDE_CODE_SUBAGENT_MODEL`. The router leaves the call alone unless it is a redo.
6. The parent's model: the conversation's, or the subagent's for a nested call. The router changes it only for your cap, a rule's `level` or a [teammate](#agent-teams).

For a workflow agent whose script names no model, `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` ranks between 1 and 2, with only effort set. `max_tier` doesn't lower these environment pins; a session-start notice names them. Under 2, 3, 4 and 6, a rule's `level` first raises a weaker model. The model must then be inside `max_tier`, allowed by `availableModels` and `deniedModels`, and not routed around, else the next stronger usable model runs, or the strongest usable one.

Effort set by the call, the agent, `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` stays. Otherwise a model the call, a rule or the agent named gets its level's effort, one step more on a model weaker than the level (at most `xhigh`). A call left on its parent's model gets none unless capped. On the session's own model, a main-conversation call gets at least the session's effort.

## The ladder

Each model stands for its lowest level; Opus named at `xhigh` or `max` stands for `hardest`.

| Level | Model | Effort |
| :- | :- | :- |
| `lookup` | Haiku | `medium` |
| `standard` | Sonnet | `medium` |
| `complex` | Opus | `high` |
| `hardest` | Opus | `xhigh` |
| `frontier` | Fable | `high` |

## Redo

Claude marks a redo by starting the `description` with "Redo:" after a failed check or a gap in a report. It climbs from the latest run of the same agent and description (case and spacing aside) that finished in this session in the last 30 minutes, else from what the call would run at unmarked: one effort step while that run was below `high`, else the next stronger usable model at its level's effort, else one more step. From Haiku at `medium`: Haiku `high`, Sonnet `medium`, Sonnet `high`, Opus `high`, Fable `high`, `xhigh`, `max`. With no step left, the redo runs as before, and Claude reads that nothing stronger is usable.

A redo naming more than that run runs as named; naming the same or less climbs anyway. It climbs past a rule's model, the agent's model and effort, and `CLAUDE_CODE_SUBAGENT_MODEL`, inside `max_tier`. When `CLAUDE_CODE_EFFORT_LEVEL`, `CLAUDE_CODE_SUBAGENT_EFFORT` or `maxEffortLevel` holds the effort, it moves to the next model; under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` it only gets the effort step, up to `max`. Where the mod runs, a redo climbs from the model and effort the run's requests carried, after any cap.

No redo applies to a workflow stage, an agent whose definition isn't found, a model outside the registry, or `route: false`, nor, unless your rule's `level` covers it, to a call naming no model to Explore or Plan, or from inside a subagent without the mod. A redo changes only its own call.

## The check before a turn ends

![The check before a turn ends.](assets/stop-checks.svg)

When Claude or a subagent changed a project file with Edit, Write or NotebookEdit and no check ran since, the `Stop` hook asks Claude once for the work's own check: tests, type check or linter for code, a recompute for figures, a reread for a document. Each ask costs a turn.

A check is a test runner, linter or type checker run through Bash (`pytest`, `npm test`, `make check`), in the session or a subagent. A build, a tool only named (`pip install pytest`) and a refused command don't count, and a shell command's file change isn't an edit. It asks once per batch of edits, never while a background task runs or when the transcript can't be read.

## Workflows and ultracode

Workflow agents are routed only where the mod runs. The Workflow tool's description carries the same table as the session note, so a script Claude writes names a model and effort for each stage. A model the script names is capped and gets its level's effort, and a stage naming none, as in a saved script written without them, runs on its parent's model. The router only raises a stage's effort, since it can't see the script's, and never redoes a stage. Under ultracode the session's effort is the floor on the session's model.

## Agent teams

A teammate keeps one model for all its tasks, so one that nothing names a model for starts on Sonnet when its parent is weaker. Its effort is set even under `CLAUDE_CODE_SUBAGENT_EFFORT`, which doesn't reach teammates. Without the mod, a named call counts as a teammate only while `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is on.

## Role agents

Five agents carry their own models, which hold with routing off: `model-router:scout` (search) and `verifier` (checks) on Haiku at `medium`, `architect` (plans) and `reviewer` (risky diffs) on Opus at `high`, and `implementer` (code to a plan) on the call's model. Only the implementer has Edit and Write.

## Compatibility

Agents from any plugin, skill or project are routed. The router never changes which agent runs and never approves a tool call, so other plugins' hooks, your permission rules and auto mode still apply.

<a id="refused-rewrites"></a>Without the mod, the router rewrites the Agent call's `model` and `effort` and tells Claude. If a rewritten call comes back unrun, its repeat passes through unchanged, or is refused with the model to use if it exceeds `max_tier`.

### Organization allowlists

The mod reads `availableModels`, `deniedModels` and `maxEffortLevel` from your settings, so a blocked model is never picked and no effort passes the cap. If the lists leave no registry model open, the call runs as it is. Without the mod, only Claude Code enforces them.

### Third-party providers

Where a provider's alias runs an older model, as on Bedrock, Vertex AI and Foundry, the mod sends `claude-opus-5-5`, `claude-sonnet-5-5` or `claude-haiku-5-5`, or the alias for a day after a refusal. `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` keeps the alias. None of these is tested live.

## What it stores

Its state is in `~/.claude/plugins/data/model-router-claude-code-model-router/`, readable only by you on macOS and Linux, and holds no prompt, brief or description. `ledger.jsonl` has a row per spawn, result, model error, check and subagent turn (agent, level, model, effort, reason, tokens, cost estimate) for 30 days, at most 4 MiB. `state.json` holds model health until `reset`. The rest track routes, redos, edits and locks for a day or the newest 200 sessions. Delete the folder to remove it all. On a Python that writes no bytecode under the launcher, as the macOS system Python does, compiled code is cached in `~/.cache/model-router/pycache`, which you can delete at any time and which uninstalling leaves behind.

## What it can't do

- It never changes the main conversation's model or effort, since a switch re-reads the conversation uncached. Use `ultrathink` or `/effort`.
- It never picks a model from the work: a call nothing names a model for runs on its parent's model, Opus on an Opus session.
- It leaves forks, resumed agents and agents whose definition it can't find alone, unless the call or your rule names a model.
- It doesn't retry a failed call; set `fallbackModel`. After a model error it suggests a usable model (not under the force variable), and after two errors, or two runs on another model, it routes around that model for a day. With the mod, a run that another mod moved to another model doesn't count.
- Where Fable bills to usage credits, a background session or teammate with nobody to answer the consent prompt ends its turn, and `claude -p` bills without asking; `max_tier` at `opus` avoids both.
- Without the mod, as where an organization blocks user mods, it has no dollar figures, workflow routing, settings lists, newest IDs or model table in the Agent and Workflow tools' descriptions. With hooks off it does nothing.
