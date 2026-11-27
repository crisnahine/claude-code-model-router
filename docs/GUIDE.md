<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Next: <a href="FAQ.md">Questions</a></p>
<!-- End of page head. -->

# Guide

How to install, use and configure Model Router 0.2.1.

## Install, update and uninstall

It needs Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`.

- Install: `/plugin install model-router --marketplace crisnahine/claude-code-model-router`, then pick a scope. From a shell, `claude plugin install` takes the same arguments and `--scope`.
- Update: `claude plugin update model-router@claude-code-model-router`, or **Update now** on the plugin in `/plugin`. Auto-update is off by default; turn it on for the marketplace in the **Marketplaces** tab.
- Disable or uninstall: `claude plugin disable` or `uninstall`, with `model-router@claude-code-model-router`. Uninstalling the last install deletes the plugin's data folder unless you pass `--keep-data`.

## Commands

- `/model-router` prints the [status line](#the-status-line), the session's model and effort once known (an estimate before its first request), and how to switch. `/model-router off` and `on` [switch routing for this session](#switch-routing-for-this-session), and `/model-router check off` and `on` set `session_check`. It answers with no model turn, and its reply is for you only.
- `/model-router:route [task]` shows the mode, cap, ladder and role agents, and Claude uses them to say which model and effort fit the task, or to delegate it.
- `/model-router:models [reset]` shows, per model, what it last ran as and whether it is working, unused, above your cap, not permitted or routed around, and what your rules files had wrong or a project's file didn't get. `reset` clears the model health record first.
- `/model-router:usage [days]` shows, over `days` (default 7), calls per agent with how many the router changed, their levels and models, redos and model errors, and, where the mod runs, tokens per model and Claude Code's cost estimate, which isn't your bill.

`models` and `usage` take `--json`.

## Configuration

### Options

Set in `/config`:

- `routing_mode`: `auto` (default) or `off`. `off` changes no call and adds no session note, Stop check or session check, but still keeps records and tells Claude of a model error or a mid-run model swap.
- `max_tier`: `none` (default), `haiku`, `sonnet`, `opus` or `fable`, the strongest model a subagent may run on. A stronger one is lowered, except an environment pin or a fork. A Mythos ID counts as Fable; a model ID the registry can't place in a family is left as it is.
- `verify_gate`: `true` (default) or `false`, the [check before a turn ends](#the-check-before-a-turn-ends).
- `session_check`: `ask` (default) or `off`, the [session check](#the-session-check). `/model-router check off` and `check on` write it too, unless managed settings own it.

Changing an option reloads Model Router, which drops the effort it set for agents still running, so where the mod runs, `/config` and `/model-router check` refuse a change while Claude is working or such an agent runs, and say why. An idle teammate holds nothing: after a change its later turns run at its own effort.

### Rules files

Your `~/.claude/model-router.json` (in `$CLAUDE_CONFIG_DIR` when set) applies everywhere and is trusted in full. A project's `.claude/model-router.json`, the nearest one up to the repository root, can only raise, so a cloned repository can't make its subagents run weaker. Both are JSON, as in `{"agents": {"my-agent": {"model": "opus"}}}`.

Agents are keyed by the name a call uses, such as `model-router:scout`. Levels are in [the ladder](#the-ladder); efforts are `low`, `medium`, `high`, `xhigh` and `max`.

- `agents.<name>.model`: yours sets it. A project's applies only when it isn't weaker than what the call would run on without it.
- `agents.<name>.level`: a floor, in either file. A call below it runs on the level's model unless the environment pins the model, and it raises the level a call that names no model gets from its agent's tools. Yours also raises an agent with `model: inherit`; a project's raises one only to a model stronger than the parent's.
- `agents.<name>.route`: `false` in yours leaves the agent alone, with no level from its tools either. A project's is ignored.
- `levels.<level>.needs` (the weakest model's strength) and `.effort`: yours sets them; a project's only raises them.
- `models.<name>.strength` and `.cost`: yours adds or re-ranks a model, named by an alias the Agent tool accepts. A project's adds only new names, used only when a call, your rule or an agent's file names them, and never under `max_tier`.

Where your rule sets an agent's `level` or `model`, a project's rule for it is ignored. `routing_mode` and values of the wrong type are dropped, and `/model-router:models` lists them with what a project's file asked for and didn't get; other keys are ignored. `max_tier` caps only the model; where the mod runs, `maxEffortLevel` in your settings caps effort.

## In a session

Where the mod runs and a person is at the prompt, Model Router adds the status line, the `/model-router` command and the session check. None of them runs under `claude -p` or the Agent SDK, and where Claude Code can't show one, it is absent and routing goes on.

### The status line

One line under the prompt, after the plugin's name:

| Part | Shows |
| :- | :- |
| `auto` or `off` | `routing_mode`, or this session's switch, marked `(this session)` while it overrides the saved option. With routing off the line is that alone |
| `mod` or `fallback` | The path that routed this session's latest call |
| `cap opus` | `max_tier`, when set |
| `14 calls` | This session's subagent, teammate and workflow agent calls, from the ledger |
| `5 changed` | The ones whose model or effort the router set; shown once there is one |
| `2 redos` | The ones that ran as redos; shown once there is one |

It refreshes when the session starts, after each spawn, after each main-loop turn and after a switch.

### Switch routing for this session

`/model-router off` leaves every call as it is from the next spawn, on the mod and the fallback alike, and `/model-router on` turns routing back on, over a saved `off` too. The reply says whether it lasts, as "Routing is off for this session. To keep it off, set Routing mode in /config." The switch ends with the session, `/clear` included, and never writes `routing_mode`. What Claude got at session start stays: after `off` the session note's table stays in its context and in the Agent tool's description, and after `on` over a saved `off` it has none.

### The session check

The recommended setups for the session that plans and delegates are in `config/models.json` under `session`, as each model's lowest effort: Opus at `medium`, Fable at `medium` and Sonnet at `xhigh`, each up to `max`. A model left out, such as Haiku, is never recommended, and neither is an older version of a family: an older full ID, or an alias the provider runs an older model under, as `sonnet` on Bedrock. Picking that family again moves to its newest model.

When a new interactive session starts below these, with `routing_mode` at `auto` and `session_check` at `ask`, Model Router asks once, after the session has started:

1. "Model Router works best with a strong session to plan and delegate. This session is on Sonnet at medium. Which model should it run on?" The options are each recommended model usable here, in the order Opus, Fable, Sonnet, then `Stay on Sonnet at medium`. The first carries `(Recommended)`, the strongest, when it isn't first, `(the strongest model)`, and Sonnet `(at xhigh effort or higher)`. A model that `availableModels` or `deniedModels` blocks is left out, and so is one that `maxEffortLevel` or `CLAUDE_CODE_EFFORT_LEVEL` holds below its lowest recommended effort. `max_tier` caps subagents, not the session, so it leaves none out.
2. After a model: "Which effort for Opus? Claude Code keeps this model and effort as your default for new sessions." The efforts run from the lowest recommended one, marked `(Recommended)`, up to `max (this session only)`, held to `maxEffortLevel`, and `medium` says `Claude Code's default`. A model with one effort left, as under `CLAUDE_CODE_EFFORT_LEVEL`, which `/effort` can't override, is offered at that effort with no second step.
3. After Stay: "Ask about this again?", with `Ask again next session` and `Don't ask again (/model-router check on brings this back)`, which sets `session_check` to `off`. When managed settings own that option, there is no second step.

Picking a setup runs Claude Code's own `/model` when the model differs, then `/effort` when the effort differs, and one line says where the session is now, as "This session is now on Opus at medium." If Claude Code doesn't switch the model, for example under an allowlist, the line says the session stayed and no effort is set, since Claude Code saves effort per model. If it switches the model but doesn't run `/effort`, the line says so. A dismissal, text typed under the dialog's own rows, or Stay changes nothing.

It asks at most once per session, a plugin reload included, and never once the conversation has a prompt in it: a prompt given at launch, or typed before the question shows, keeps it away. Before the first request the session's effort is an estimate: `CLAUDE_CODE_EFFORT_LEVEL` (under `auto` or `unset`, the API's default for the model, `medium` on Opus and `high` on Sonnet and Fable, or `medium` lowered to `maxEffortLevel` where that sets a cap), then a `--effort` flag at launch (`ultracode` reads as `xhigh`), then the settings files as Claude Code reads them (a model's own level before a top-level `effortLevel`), then `medium`, held to `maxEffortLevel`. Where the session's command line can't be read, the estimate goes without the flag, and it never sees an organization's default effort. From the first request on, the main loop's own effort is read, and `/model-router` shows it, as `session: opus at medium` or `session: sonnet at medium (below the recommended setups)`.

## Who decides the model

![Who decides the model, strongest first.](assets/precedence.svg)

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` runs every subagent on `CLAUDE_CODE_SUBAGENT_MODEL`, else the conversation's model. With only `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` set, the built-in Explore subagent runs on the model listed for it. The router sets only effort, and none on a call that names a model.
2. A model named on the call, by Claude, a skill or a workflow script. When your prompt asks for one ("use opus"), Claude gets a note to pass it.
3. A per-agent rule: yours, or a project's when not weaker.
4. The agent's own `model` field. `inherit` there is the agent's choice of the parent's model, and it outranks 5.
5. `CLAUDE_CODE_SUBAGENT_MODEL`. The router leaves the call alone unless it is a redo. Unforced, the variable doesn't move the built-in Explore and Plan, so they go on to 6.
6. The agent's tools. An agent whose `tools` hold none of Edit, Write, NotebookEdit, Bash, PowerShell and Monitor, or whose `disallowedTools` remove them all, as the built-in Explore and Plan, runs at `lookup`; any other at `standard`. A tool list Model Router can't read with confidence counts as every tool. Only the agent's definition counts here, never the brief. A [teammate](#agent-teams) starts no lower than `standard`.

For a workflow agent whose script names no model, `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` ranks between 1 and 2, with only effort set. `max_tier` doesn't lower these environment pins, and a notice at session start names any that is set. Under 2, 3, 4 and 6, a rule's `level` first raises a weaker model. The model must then be inside `max_tier`, allowed by `availableModels` and `deniedModels`, and not routed around, else the next stronger usable model runs, or the strongest usable one.

Effort set by the call, the agent, `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` stays. Otherwise a model from 2, 3, 4 or 6 gets its level's effort, one step more on a model weaker than the level needs (at most `xhigh`). An `inherit` agent left on its parent's model gets none. On the session's own model, a call from the main conversation gets at least the session's effort.

## The ladder

Each model stands for its lowest level; Haiku named at `high` or above stands for `standard`.

| Level | Model | Effort |
| :- | :- | :- |
| `lookup` | Haiku | `medium` |
| `standard` | Haiku | `high` |
| `complex` | Sonnet | `high` |
| `hardest` | Opus | `high` |
| `frontier` | Fable | `high` |

To run a level on a stronger model, raise its `needs` to that model's strength (Haiku 1, Sonnet 2, Opus 3, Fable 4) in your `~/.claude/model-router.json`. This puts `complex`, and with it the session note's row for a design or a review, on Opus:

```json
{"levels": {"complex": {"needs": 3}}}
```

## Redo

Claude marks a redo by starting the `description` with "Redo:" after a failed check or a gap in a report. It climbs from the latest run of the same agent and description (case and spacing aside) that finished in this session in the last 30 minutes, else from what the call would run at unmarked: one effort step while that run was below `high`, else the next stronger usable model at its level's effort, else one more effort step. From Haiku at `medium`: Haiku `high`, Sonnet `high`, Opus `high`, Fable `high`, `xhigh`, `max`. With no step left, it runs as that run did, and Claude reads that nothing stronger is usable.

A redo that names more than that run runs as named; one that names the same or less climbs anyway. It climbs past a rule's model, the agent's model and effort, and `CLAUDE_CODE_SUBAGENT_MODEL`, inside `max_tier`. When `CLAUDE_CODE_EFFORT_LEVEL`, `CLAUDE_CODE_SUBAGENT_EFFORT` or `maxEffortLevel` holds the effort, it moves to the next model; under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` it gets only the effort step, up to `max`. Where the mod runs, it climbs from the model and effort the run's requests carried, after any cap.

No redo applies to a workflow stage, an agent whose definition isn't found, a model outside the registry, or `route: false`. A redo changes only its own call.

## The check before a turn ends

![The check before a turn ends.](assets/stop-checks.svg)

When Claude or a subagent changed a project file with Edit, Write or NotebookEdit and no check ran since, the `Stop` hook asks Claude once for the work's own check: tests, type check or linter for code, a recompute for figures, a reread for a document. Each ask costs a turn.

A check is a test runner, linter or type checker run through Bash (`pytest`, `npm test`, `make check`), in the session or a subagent. A build that only compiles, a tool only named (`pip install pytest`) and a refused command don't count, and a shell command's file change isn't an edit. It asks once per batch of edits, and never while a background task runs or when the transcript can't be read.

## Workflows

Workflow agents are routed only where the mod runs. The Workflow tool's description carries the session note's table, which asks a script to name a model and effort for each stage. A model the script names is capped, and a stage that names none, as in a saved script written without them, runs on its agent's own model, else on `CLAUDE_CODE_SUBAGENT_MODEL` when that is set, else at the level its tools put it. The router raises a stage's effort to its level's and never lowers it, since it can't see the one the script set, and never redoes a stage.

## Agent teams

A teammate keeps one model for all its tasks, so one that nothing names a model for starts no lower than the `standard` level's model, even when its agent can only read. Its effort is set even under `CLAUDE_CODE_SUBAGENT_EFFORT`, which doesn't reach teammates. Without the mod, a named call counts as a teammate only while `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is on.

## Role agents

Five agents carry their own models, which hold with routing off: `model-router:scout` (search) and `verifier` (checks) on Haiku at `medium`, `implementer` (code to a plan) on Haiku at `high`, and `architect` (plans) and `reviewer` (risky diffs) on Sonnet at `high`. Only the implementer has Edit and Write.

## Compatibility

Agents from any plugin, the project or `~/.claude/agents` are routed, and Claude Code's built-ins. The router never changes which agent runs and never approves a tool call, so other plugins' hooks, your permission rules and auto mode still apply.

Without the mod, the router rewrites the Agent call's `model` and `effort` and tells Claude. If a rewritten call comes back unrun, its repeat passes through unchanged, or is refused with the model to use if it would run above `max_tier`.

### Organization allowlists

The mod reads `availableModels`, `deniedModels` and `maxEffortLevel` from your settings, so the router never picks a blocked model or sets an effort past the cap. If the lists leave no registry model open, the call runs as it is. Without the mod, only Claude Code enforces them.

### Third-party providers

Where a provider's alias runs an older model (the providers and families under `older_aliases` in `config/models.json`, such as `sonnet` and `haiku` on Bedrock), the mod sends the family's newest ID, `claude-sonnet-5-5` or `claude-haiku-5-5`. The table is the one Claude Code 2.1.332 runs. On 2.1.329 to 2.1.331, `sonnet` on Google Cloud's Agent Platform and `opus` and `sonnet` on Microsoft Foundry run an older model too, and the router sends them as the alias. It sends the alias instead when `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` is set, when `availableModels`, `deniedModels` or `modelOverrides` rule the ID out, and for a day after the provider refuses it. Where no newest ID goes out, as on the fallback path, the router's own pick moves one family up from a lagging alias, unless that variable is set. None of these has had a live run.

## What it stores

Its state is in `~/.claude/plugins/data/model-router-claude-code-model-router/`, readable only by you on macOS and Linux, and holds no prompt, brief or description. `ledger.jsonl` has a row per spawn, result, model error, Stop-check ask and subagent turn (agent, level, model, effort, reason, tokens, cost estimate), kept 30 days and at most 4 MiB. `state.json` holds model health until `reset`. The other files hold each session's routes, finished runs, edits, settings, switch and session check for the newest 200 sessions, routes, runs and switches for a day at most, and `locks.json` counts lock waits. Delete the folder to remove it all. On a Python that writes no bytecode under the launcher, as the macOS system Python does, compiled code is cached in `~/.cache/model-router/pycache`, which you can delete at any time and which uninstalling leaves behind.

## What it can't do

- It never changes the main conversation's model or effort by itself, since a switch re-reads the conversation uncached. Only a setup you pick in the [session check](#the-session-check) runs `/model` and `/effort`; otherwise run them yourself.
- It leaves forks and resumed agents alone, and an agent whose definition it can't find unless the call or your rule names a model. It finds a plugin's agent through Claude Code's install record, so a plugin with no record, such as one loaded with `--plugin-dir`, isn't found.
- It doesn't retry a failed call; set `fallbackModel` for that. After a model error it tells Claude a usable model to re-issue the call on (not under the force variable), and after two errors, or two runs on another model, it routes around that model for a day. Where the mod runs, a run that another mod moved to another model doesn't count.
- Where Fable bills to usage credits, a subagent or teammate on Fable can meet Claude Code's consent prompt: with nobody to answer, as in a background session or a teammate's session, the turn ends at the prompt's deadline, and `claude -p` bills without asking. `max_tier` at `opus` keeps subagents off Fable.
- Without the mod it has no dollar figures, workflow routing, reading of `availableModels`, `deniedModels` or `maxEffortLevel`, newest IDs, table in the Agent and Workflow tools' descriptions, status line, `/model-router` or session check. Its state is then in `/config`, `/model-router:models` and `/model-router:usage`.
- Under `claude --bare` Claude Code runs no plugin hooks or mods, so no call is routed.
