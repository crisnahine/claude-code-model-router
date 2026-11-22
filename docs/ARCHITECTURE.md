# Architecture

## The idea

Model Router has one decision point, in front of the `Agent` tool. For every call, from any plugin, skill or agent, it works out which model and reasoning effort the subtask needs and writes those two fields onto the call before it runs. The agent the caller chose, its prompt and its tools stay as they were.

This rests on one documented Claude Code mechanism: a `PreToolUse` hook may return `updatedInput`, which replaces a tool call's arguments. The per-call `model` parameter outranks every other source of a subagent's model except a forced environment setting, so setting it is enough.

## Why routing happens on the call

A common way to build multi-model orchestration is to ship a set of agents, each pinned to a model, and steer work toward them. That covers only those agents. An agent from another plugin, or one you wrote, gets no routing. Steering also means refusing calls to other agents, and each refusal costs a round trip and competes with other plugins over which agent should run.

Writing the model onto the call avoids all of that. Every agent is covered, including ones this plugin has never seen, and no call has to be refused to get there.

## Components

```text
                    ┌──────────────┐   config/models.json + model-router.json files
                    │   config     │   options from /config (cap, priority, mode, pins)
                    └──────┬───────┘
     agent call            │
  ┌──────────────┐  ┌──────▼───────┐  ┌──────────────┐
  │   agents     ├─►│   router     │◄─┤   registry   │  models as data; what is usable here:
  │ definition   │  │  decide()    │  │ select/clamp │  cap, settings allowlists, health
  └──────────────┘  └──┬───────▲───┘  └──────▲───────┘
  ┌──────────────┐     │       │             │ results and failures
  │   profile    ├─────┘  ┌────┴─────┐  ┌────┴─────┐
  │ level + why  │        │  learn   │  │  hooks   │  one handler per hook event
  └──────────────┘        │ feedback │  └────┬─────┘
                          └──────────┘       │
  ┌──────────────┐  ┌──────────────┐  ┌──────▼───────┐
  │   gates      │  │  upstream    │  │    store     │  state files, locks and the ledger
  │ before Stop  │  │ releases,    │  └──────────────┘
  └──────────────┘  │ docs, models │
                    └──────────────┘
```

| Module | Job |
| :- | :- |
| `registry` | Which models exist, which are usable right now, and the `select` and `clamp` rules |
| `profile` | Turns a brief and an agent's name into a level and the reasons for it |
| `router` | Applies the order of authority and produces one decision per call |
| `learn` | Counts votes that a level was routed too low, and raises it |
| `agents` | Finds an agent's definition in the project, the user folder, this plugin or another installed plugin |
| `session` | Tracks the model the conversation itself runs on |
| `gates` | The two checks before a turn ends |
| `upstream` | Compares releases, the docs index and model aliases with what was last reviewed |
| `hooks`, `cli`, `report`, `store`, `config` | Hook handlers, commands, tables, files and options |

All of it is standard-library Python. No module names a model: the four shipped models are entries in `config/models.json`, so a different selection rule or a better classifier can replace `registry.select` or `profile` without touching the rest.

## The decision

`router.decide` returns one record: keep, rewrite or deny, plus the model, the effort, the level and the reason. The reason is written to the ledger, so every choice can be explained afterwards.

Order of authority, strongest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`: the user pinned every subagent, so nothing is changed.
2. A model named on the call. Claude, the user through Claude, or a skill chose it.
3. A per-agent rule in a `model-router.json` file.
4. The agent's own `model`, unless `agent_pins` is `override`.
5. `CLAUDE_CODE_SUBAGENT_MODEL`, the user's default for agents without a model.
6. The router's pick: `registry.select(level needs + learned raise, priority, usable models)`.

Then `registry.clamp` lowers the result to the nearest usable model. It never raises. Effort is set only when the router made the pick, the caller passed none, the agent defines none, and `CLAUDE_CODE_EFFORT_LEVEL` isn't set.

Some details of how the rules apply:

- If the result is the model the agent would run on anyway and no effort is being added, the hook prints nothing.
- An agent whose definition can't be found is left alone. It may have a model its author chose, and overriding that blind would be worse than not routing it.
- Inside a subagent, and for the built-in Explore and Plan agents, the model a call would inherit isn't known for certain, so the pick is always written onto the call.
- A model the user names in a prompt is passed to Claude as a reminder to put it on the calls for that request. Words like "opus" appear in ordinary sentences too, so the hook leaves that judgment to Claude and forces nothing.

### Rewrites that don't take

The `Agent` tool validates a rewritten call, and Claude Code reports no event when it rejects one. A hook that rewrote the retry the same way would loop. So every rewrite is remembered until its result or failure arrives. If the same call shows up again first, the earlier rewrite didn't run, and the call is passed through untouched. When the target was a model that has never run in this setup, that counts as a strike against it, and two strikes route around it.

One case can't be passed through. If the call would run above the user's cap, letting it go untouched would break the cap. That repeated call is denied, with the model to use in the reason. Claude re-issues it with that model, and the router has nothing left to change. This is the only denial the plugin ever returns.

### Permissions

The hook returns `updatedInput` and a one-line note for Claude. It never returns `permissionDecision: "allow"`. Approving the call would skip the user's ask rules and auto mode's review of the spawn, and choosing a model is no reason to do that.

## Discovery and adaptation

| What | How it is discovered | What happens |
| :- | :- | :- |
| The models that exist | `config/models.json`, plus the user's and a project's additions | They are the candidates |
| The models you allow | The `max_tier` option | Stronger ones are never picked, and pins and explicit choices are lowered |
| The models your organization allows | `availableModels` and `deniedModels` in user, project and managed settings | Blocked ones are never picked |
| The model a name really resolves to | `resolvedModel` in the `Agent` tool's result | Shown in `/model-router:models` |
| A model that isn't served here | A model error from the `Agent` tool, twice | Routed around for a day, then retried. Repeat trouble extends the wait to three days, then seven |
| A model that is silently replaced | Asked for one, `resolvedModel` is another, twice | Same |
| A model name the `Agent` tool rejects | A rewrite to it comes back without having run, twice | Same |
| A level routed too low | Claude redoes a routed call on a stronger model, or names a stronger model than the pick | After 6 votes that amount to 30% of that level's calls, the level is raised one step |
| A new or removed model alias | The alias table on the docs' model configuration page, fetched daily | Reported at session start and by `/model-router:refresh`. The repo's daily workflow opens an issue |
| A Claude Code release, or a change to a docs page this plugin relies on | Changelog RSS and `llms.txt`, fetched daily | Same |

A new model family is reported but not routed to until it has a strength rating. Placing an unknown model on the ladder by guesswork would send it work it may be too weak or too costly for. Feedback also never takes a level below what ships, and a raise is undone when the raised level keeps being given a weaker model.

## The conversation's own model

A plugin can't switch the model of the main conversation. Two hooks stand in for that:

- `UserPromptSubmit` profiles each request. When the request needs more than the session's model, Claude is told to hand the hard thinking to a strong subagent. Otherwise the hook says nothing, so a normal prompt carries no extra tokens.
- `Stop` checks whether that happened. A complex or hardest request on a weaker session, with no agent of the needed strength started, blocks the stop once and asks for a strong agent to check the result.

A second check at `Stop` asks for verification once, when code changed and no test, lint, type-check or build command ran after the last edit and no verifier or reviewer was started.

## Hook flow

```text
SessionStart ─┬─ session-start   records options and the session model; explains automatic routing
              └─ refresh-cache   (async) refreshes the upstream cache if older than 24 h
UserPromptSubmit ─ user-prompt   profiles the request; notes a model the user may be asking for
PostModelSwitch ─ model-switch   records the session's new model
PreToolUse (Agent|Task) ─ pre-agent      router.decide; returns updatedInput when it changes something
PostToolUse (Agent|Task) ─ post-agent    (async) records the model that really ran
PostToolUseFailure (Agent|Task) ─ agent-failed   counts model errors
PostToolUse (Edit|Write|NotebookEdit) ─ post-edit   notes that code changed
Stop ─ stop                      the two checks
```

Every handler catches its own errors and exits 0. The launcher exits 0 when Python is missing. A bug in this plugin can cost routing, but it can't block a tool call or a session.

## State

In `${CLAUDE_PLUGIN_DATA}`:

| File | Contents |
| :- | :- |
| `state.json` | Recorded options and model health |
| `upstream.json` | The upstream cache and what was last reviewed |
| `learned.json` | Calls and votes per level, and which levels are raised |
| `sessions.json`, `prompts.json`, `edits.json`, `pending.json` | Per session: its model, the current request's profile and routed calls, edit and check times, and rewrites waiting for a result. The 200 most recently written sessions are kept |
| `ledger.jsonl` | One line per agent call and per result, with the decision and its reason. Trimmed at 4 MB |

Writes are atomic. Each read-modify-write cycle takes a lock file, because agent calls issued in one message run their hooks at the same moment. A lock that can't be had within two seconds is skipped, so a hook never hangs a session.

Nothing leaves the machine. The only network access is the daily fetch from `code.claude.com`.

## Trust

The user's own `~/.claude/model-router.json` is trusted in full. A repository's `.claude/model-router.json` is read like any project file. It can turn routing down to `advise` or `off`, set rules for agents, and add models. It can't switch routing on for a user who turned it off, and it can't change the strength of a model that ships or that the user defined. Models it adds are ignored while the user has a cap set, since their rank is only the repository's claim.

## What is out of reach

- The main conversation's model.
- Skills that run in their own fork, workflow agents and agent teams. They don't go through the `Agent` tool call this plugin hooks.
- Agents with no definition on disk (command-line agents, plugins loaded with `--plugin-dir`), unless `agent_pins` is `override`.
- Answer quality. The profile is an estimate from the brief's wording and the agent's name, and the feedback loop reacts to what Claude redoes. Neither measures whether an answer was right.
- Sessions where hooks are disabled by `disableAllHooks` or `allowManagedHooksOnly`.

## Verified live

On Claude Code 2.1.330 unless noted:

- A Sonnet session started two `general-purpose` agents without a model in one message. The lookup ran on Haiku at low effort and the security review on Opus at high effort. The same happened in an interactive terminal session in auto mode, with no denial.
- An Opus session in auto mode used another installed plugin's agents. The one without a model ran on Haiku at low effort, and the one that names Haiku was left as is.
- With a cap of Sonnet, `model-router:deep-solver`, which names Fable, ran on Sonnet with no refusal.
- With a registry entry for a model name the `Agent` tool doesn't accept, the first rewrite was rejected and the repeated call ran untouched. After the second rejection the model was set aside, and the next call went to the strongest model that works.
- A Haiku session was given a hard design request. The check before finishing fired, and `model-router:deep-solver` on Fable reviewed the answer.
- On Claude Code 2.1.270 the rewrite applied and the agent ran on Haiku.
