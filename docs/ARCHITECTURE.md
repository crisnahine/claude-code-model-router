# Architecture

## The idea

Model Router decides the model and reasoning effort of every agent Claude Code starts: subagents from any plugin, skill or agent, teammates, and workflow stages. It also raises the main conversation's effort for a hard request. The agent the caller chose, its prompt and its tools stay as they were.

The aim is the lowest cost per finished task at a high quality bar. Every subtask starts on the cheapest model and effort that should do it, and moves up only when its result comes back for a redo. That ordering comes from Anthropic's own measurements: running at low effort and re-running failures at high solved about 97% of a coding benchmark for $0.17 a task, against 95.3% for $0.29 with every task at high ([docs/research/model-control.md](research/model-control.md), Q7).

## Two control paths, one router

```text
              Claude Code engine events                      settings hooks
   agent.spawn  turn.start  turn.step  turn.complete      SessionStart, UserPromptSubmit,
        │           │           │            │            PreToolUse, PostToolUse(Failure),
        └───────────┴─── hooks/router.ts ────┘            PostModelSwitch, SubagentStop, Stop
                            (the mod)                               │
                                │   sh model-router.sh mod-*        │  sh model-router.sh <event>
                                └──────────────┬────────────────────┘
                                               ▼
                                  scripts/modelrouter (Python)
                  spawn · router.decide · ladder · registry · profile · calls · learn · gates
```

**The mod** (`hooks/router.ts`, a hooks module loaded from `hooks/hooks.json` under `modules`) is the primary path. It hooks four engine events:

| Event | What the mod does |
| :- | :- |
| `agent.spawn` | Fires for every subagent, teammate and workflow agent before its model is resolved. The mod asks the router and passes the pick on as `model`. The event names the parent's effective model exactly (`parentModel`), so nothing is guessed from a transcript. Forks and resumed runs are left alone. |
| `turn.step` | Fires for each model request, the main loop's and each agent's. `agent.spawn` has no effort field, so a routed agent's effort is set here, on each of its requests. The main loop's turn effort is applied here too. Each request's usage is kept for its turn. |
| `turn.start` | The main loop's request text, after the settings hooks for the prompt ran. The router picks that turn's effort from it. |
| `turn.complete` | Each turn's token usage and model, main loop and agents alike, written to the ledger with its cost: the sum of its requests, each priced on its own, because Haiku 5.5 bills a prompt over 100,000 tokens at a higher rate per request (https://platform.claude.com/docs/en/about-claude/pricing.md, Long context pricing). A turn sent without its requests is priced at the base rate. |

The mod calls the same Python runtime the settings hooks use, through `$.process.run` with the plugin's hook environment, so there is one decision procedure and one state store. A spawn decision settled in about 110 ms in live runs, the time to start Python included.

**The settings hooks** keep every job the mod doesn't do: the session-start note, the request profile for the conversation's own model, availability tracking from each `Agent` result, redo bookkeeping, and the two checks before a turn ends. `PreToolUse` on the `Agent` tool is the fallback path. It rewrites the call's `model` and `effort` with `updatedInput`, and stands down once the mod has announced itself for the session (`mod-start`), so a call is never routed twice. The fallback covers Claude Code versions without mods and organizations that don't load user mods.

Why the mod comes first: it reaches teammates and workflow stages, which the `Agent` call doesn't carry. It sets the model after auto mode reviewed the call, so auto mode's "input changed after review" denial doesn't apply. And it can set effort on each request, which a forced subagent model leaves as the only lever.

## Components

| Module | Job |
| :- | :- |
| `registry` | Which models exist, which are usable right now, the `select` and `clamp` rules, and list prices |
| `profile` | Turns a brief and an agent's name into a level and the reasons for it |
| `ladder` | Turns a level, the priority and learned raises into the pick: a model and an effort |
| `spawn` | The one procedure both paths call for a spawn: gathers the facts, asks `router.decide`, writes the ledger row, and records the call |
| `router` | Applies the order of authority and produces one decision per agent, from the facts it is given alone |
| `calls` | What happened to each call: routed, rewritten and waiting, finished. Answers the redo, refused-rewrite and strongest-spawn questions |
| `learn` | Decides from each spawn's facts which votes it casts, counts votes that a level was routed too low, and raises it |
| `agents` | Finds an agent's definition in the project, the user folder, this plugin or another installed plugin |
| `session` | Tracks the model the conversation itself runs on |
| `gates` | The two checks before a turn ends |
| `upstream` | Compares releases, the docs index, model aliases and model status with what was last reviewed |
| `settings` | The plugin's options and the environment facts routing depends on, resolved once per hook call or command |
| `hooks`, `cli`, `report`, `store`, `config` | Handlers for settings hooks and mod calls, commands, tables, state files, and the registry and override files |

All of it is standard-library Python, plus the one TypeScript file the engine runs. No module names a model: the shipped models, and aliases such as `best`, are entries in `config/models.json`.

## The ladder

| Level | Needs | Model (balanced) | Effort |
| :- | -: | :- | :- |
| lookup | 1 | Haiku | low |
| standard | 2 | Sonnet | high |
| complex | 3 | Opus | high |
| hardest | 3 | Opus | xhigh |
| frontier | 4 | Fable | high |

Each step is set from the measurements in the research notes:

- Standard work runs Sonnet at `high`. Claude Code's default for Sonnet 5.5 is `medium`, which scored about 7 points lower on coding.
- Complex and hardest share Opus and differ in effort. Effort is the cheaper lever: on current models it keeps the prompt cache, and Opus 5.5 at `high` came within a point of Fable 5.2 at `high` for under a quarter of the cost.
- Fable is kept for frontier work, where the brief is hardest and also open-ended or research-grade, and for redos of hardest work.

A level's `needs` is a strength. `ladder.pick` adds any learned raise, and `registry.select` takes the cheapest usable model at or above it, so a cap or an unavailable model moves the pick without any level naming a model.

## The decision

Both paths call one procedure, `spawn.run`. It gathers the facts about the call (the agent's definition, the model it would inherit, the usable models, and an earlier finished run of the same call), passes them to `router.decide`, writes one ledger row (`via` says which path), and records the call in `calls` and its votes in `learn`. Each path is a thin adapter around it: the fallback turns the decision into `updatedInput` and a note, the mod into `{model, effort, reason}`.

`router.decide` reads no state of its own: the parent's model, the agent's definition, the call's earlier run and the learned raises all arrive as facts. It returns one record: keep or rewrite, plus the model, the effort, the level and the reason. The reason is written to the ledger, so every choice can be explained afterwards.

Order of authority, strongest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`: the user pinned every subagent's model. The router sets effort only.
2. A model named on the call: by Claude, by the user through Claude, by a skill, or by a workflow script.
3. A per-agent rule in a `model-router.json` file.
4. The agent's own `model`, unless `agent_pins` is `override`.
5. `CLAUDE_CODE_SUBAGENT_MODEL`, the user's default for agents without a model.
6. The router's pick: `ladder.pick(level, priority, usable models)`, which runs `registry.select` on the level's needs plus any learned raise.

Then `registry.clamp` lowers the result to the nearest usable model. It never raises.

Effort is set when the router, a rule or the agent's own model decided the model and nobody chose an effort: not the call, not the agent's definition, not `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT`. A pin is about the model, and an agent that pins Opus with no effort would otherwise run at `medium`.

Some details of how the rules apply:

- If the result is the model and effort the agent would run on anyway, nothing is changed.
- An agent whose definition can't be found is left alone, since it may have a model its author chose. Workflow stages and teammate roles are the exception: the engine makes them, and they name no model unless the script or the call does.
- On the fallback path, inside a subagent and for the built-in Explore and Plan agents, the model a call would inherit isn't known for certain, so the pick is always written onto the call. The mod path knows the parent's model and needs no such guess.
- A call that names a model outside the registry, such as `inherit` or a gateway's own ID, is left as it is.
- A model the user names in a prompt is passed to Claude as a reminder to put it on the calls for that request. Words like "opus" appear in ordinary sentences too, so the hook leaves that judgment to Claude and forces nothing. A call on that model casts no feedback votes, since the user chose it, not Claude.

### Redo escalation

When an agent's result comes back, the call is remembered with the level it ran at (`done`, per session). For an agent that runs in the background the `Agent` call returns as soon as the agent has launched, so its call waits under the agent's id (`launched`) and counts as finished when the agent stops with an answer. Copies of one brief started together therefore all run at the brief's own level. The `post-agent` hook runs before Claude's next step, so a redo always finds the record. If the same call is issued again, the first answer wasn't good enough, so it runs one level higher, and the redo is a vote that its first level was too low. On the shipped ladder (`levels` in `config/models.json`), lookup to standard, standard to complex and hardest to frontier move to a stronger model, and complex to hardest stays on Opus and raises effort to xhigh. Any redo that stays on the model of the run it replaces, as complex to hardest does, or under a cap that holds it there, or for a frontier run with no level above it, runs at least one step more effort than that run (`router._above_earlier`). A call that names its own model is never escalated: the caller has decided.

### Rewrites that don't take

On the fallback path, the `Agent` tool validates a rewritten call, and Claude Code reports no event when it rejects one. A hook that rewrote the retry the same way would loop. So every rewrite is remembered until its result or failure arrives. If the same call shows up again first, and more than a few seconds later, the earlier rewrite didn't run, and a call the router would give a model is passed through untouched. Copies of one call issued in one message arrive together, so they are each routed as usual. This guard belongs to the fallback adapter alone; `router.decide` doesn't know about it. When the target was a model that has never run in this setup, that counts as a strike against it, and two strikes route around it.

One case can't be passed through. If the model the call would run on untouched is above the user's cap, letting it go would break the cap. That repeated call is denied, with the model to use in the reason. This is the only denial the plugin ever returns, and the mod path never needs it.

### Permissions

Neither path approves anything. The hook returns `updatedInput` and a one-line note, never `permissionDecision: "allow"`, and the mod's spawn hook only passes a model on. The user's ask rules and auto mode's review of the spawn apply as they would without the plugin.

## The conversation's own model

A mod could send the main loop's requests to another model, and the first probe of this design did. The router doesn't, by choice. Each model keeps its own prompt cache, so a switch re-reads the whole conversation uncached, and switching back does it again. Effort keeps the cache on current models with an API key or a subscription, so the main loop gets effort instead:

- `turn.start` profiles the request. A complex, hardest or frontier request raises that turn's effort (`main.effort` in the registry). It only raises: a level below what the session already asks for is left alone, so `/effort`, `ultrathink` and the user's own settings win. It is skipped when `CLAUDE_CODE_EFFORT_LEVEL` is set, when the `main_effort` option is off, and wherever an effort change breaks the cache (https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level): on Bedrock, Vertex and Foundry, behind `ANTHROPIC_BASE_URL` (a gateway), with `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` set, and when the session's model is known and isn't Fable 5.1 or later, Opus 5.5, Sonnet 5.5 or Haiku 5.5. An unknown model, as after `/clear` when SessionStart sends none, is raised as before. The model is the mod's `$.session.model()` when that is a model ID, else what SessionStart or PostModelSwitch recorded.
- `UserPromptSubmit` profiles each request against the session's model. When the request needs more than that model, Claude is told to hand the hard thinking to a strong subagent. Otherwise the hook says nothing, so a normal prompt carries no extra tokens.
- `Stop` checks whether that happened. A complex or harder request on a weaker session, with no agent of the needed strength started, blocks the stop once and asks for a strong agent to check the result.

A second check at `Stop` asks for verification once, when code in the project (under the hook's `cwd` or `CLAUDE_PROJECT_DIR`) changed and no test, lint, type-check or build command ran after the last edit and no verifier or reviewer was started.

## Discovery and adaptation

| What | How it is discovered | What happens |
| :- | :- | :- |
| The models that exist | `config/models.json`, plus the user's and a project's additions | They are the candidates |
| New versions of a model | Nothing to discover: routing uses the aliases, and Claude Code moves each alias to the newest version | Picked up the day Claude Code ships them |
| A setup running behind the newest version | The session's model and what each alias resolved to, against the newest active version of each family in the model status data | Named at session start: a `model` setting that pins a full version, or a Claude Code that hasn't moved its alias yet |
| The models you allow | The `max_tier` option | Stronger ones are never picked, and pins and explicit choices are lowered |
| The models your organization allows | `availableModels` from managed settings alone, else user, project and local settings combined; `deniedModels` from managed settings only. An allow entry stands for its family; a deny entry counts when it names a family | Blocked ones are not picked, unless nothing in the registry is left open |
| The model a name resolves to | `resolvedModel` in the `Agent` tool's result, and the model on each turn's usage | Shown in `/model-router:models` and `/model-router:usage` |
| A model that isn't served here | A model error from the `Agent` tool, twice | Routed around for a day, then retried. Repeat trouble extends the wait to three days, then seven |
| A model that is silently replaced | The model a call was routed to, or the one it named, against `resolvedModel`, twice. Not counted while `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` is set | Same |
| A model name the `Agent` tool rejects | A rewrite to it comes back without having run, twice | Same |
| A level routed too low | A redo of a routed call, or Claude naming a stronger model than the pick | Redos run one level up at once. After 6 votes that amount to 30% of that level's calls, the level is raised for good |
| A new or removed model alias | The alias table on the docs' model configuration page, fetched daily | Reported at session start and by `/model-router:refresh`. The repo's daily workflow opens an issue |
| A model being deprecated or retired | The "Model status" table on the platform's model deprecations page, and the account's Models API list when an API key is set, fetched daily | A model routed calls ran on is reported at session start with its retirement date and replacement. Any newly deprecated model is reported by `/model-router:refresh` and the daily workflow |
| A Claude Code release, or a change to a docs page this plugin relies on | Changelog RSS and `llms.txt`, fetched daily | Reported the same way |
| A model with no price | A turn's usage names a model the `prices` table lacks | Listed by `/model-router:usage` |

A new model family is reported but not routed to until it has a strength rating. Placing an unknown model on the ladder by guesswork would send it work it may be too weak or too costly for. Feedback never takes a level below what ships, and a raise is undone when the raised level keeps being given a weaker model.

With `ANTHROPIC_API_KEY` in the environment and no `ANTHROPIC_BASE_URL` or cloud provider in between, the daily check also reads the Models API (`GET /v1/models`). Its `line`, `lifecycle` and `retires_at` for each model the account can call take precedence over the public page, and a new `line` is reported as a new model family. The key is sent to `api.anthropic.com` alone, and that request follows no redirect. A subscription login gives hooks no key, so those setups rely on the public page.

A changelog or docs index that comes back empty, such as an outage page, counts as a failed fetch. The last cache stays as it was, and the repo's daily workflow fails instead of opening an issue.

## Hook flow

```text
mod  session.start ─ mod-start      marks the session, so PreToolUse stands down
mod  agent.spawn   ─ mod-spawn      spawn.run for a subagent, teammate or workflow stage
mod  turn.start    ─ mod-turn       the main loop's effort for this turn
mod  turn.step                      applies the agent's or the turn's effort to each request; keeps its usage
mod  turn.complete ─ mod-usage      prices the turn's requests into the ledger
SessionStart ─┬─ session-start   records options and the session model; explains routing
              └─ refresh-cache   (async) refreshes the upstream cache if older than 24 h
UserPromptSubmit ─ user-prompt   profiles the request; notes a model the user may be asking for
PostModelSwitch ─ model-switch   records the session's new model
PreToolUse (Agent|Task) ─ pre-agent      fallback: spawn.run, returns updatedInput
PostToolUse (Agent|Task) ─ post-agent    the model that ran; a returned result finishes the call for redos
SubagentStop ─ agent-stopped    a background agent's result is back; finishes its call for redos
PostToolUseFailure (Agent|Task) ─ agent-failed   counts model errors
PostToolUse (Edit|Write|NotebookEdit) ─ post-edit   notes that code changed
Stop ─ stop                      the two checks
```

Every handler catches its own errors and exits 0, and every mod hook has a `.catch` that passes the event on unchanged. The launcher exits 0 when Python is missing. A bug in this plugin can cost routing, but it can't block a tool call, a spawn or a session.

## State

In `${CLAUDE_PLUGIN_DATA}`:

| File | Contents |
| :- | :- |
| `state.json` | Every plugin option as SessionStart recorded it, for skill commands, which run without them; and model health |
| `upstream.json` | The upstream cache and what was last reviewed |
| `learned.json` | Calls and votes per level, and which levels are raised |
| `sessions.json`, `prompts.json`, `edits.json`, `pending.json`, `launched.json`, `done.json`, `mod.json` | Per session: its model, the current request's profile and routed calls, edit and check times, rewrites waiting for a result, background agents still running, finished calls for redo escalation, and whether the mod is running. The 200 most recently written sessions are kept |
| `ledger.jsonl` | One line per agent start, result, turn effort and turn usage, with the decision, its reason and the list-price cost. Trimmed at 4 MB |

Writes are atomic, and the state files are written as compact JSON. Each read-modify-write cycle takes a lock file, because agents started in one message run their hooks at the same moment. A lock that can't be had within two seconds is skipped, so a hook never hangs a session.

Each JSON state file carries a top-level `schema` version, which reads strip. `store.MIGRATIONS` is the one place an older version is brought up to date, one version per step; files from 0.1.0 have no version and are read as version 0. A file from a newer release is read but never written or removed, `/model-router:models` names it, and `models --reset` leaves it and says so. A file that fails to parse, isn't a JSON object, or has a `schema` that isn't a whole number of 0 or more is moved aside as `<name>.corrupt-<UTC timestamp>` before the next write replaces it, routing carries on with that state empty, and `/model-router:models` lists the files set aside. A file that can't be read, or can't be moved aside, is left as it is and not written. Your own settings and `model-router.json` files are only ever read, never moved. The ledger is a log of independent lines, so it has no version: a line that doesn't parse is skipped.

Nothing about your work leaves the machine. The only network access is the daily fetch from `code.claude.com` and `platform.claude.com`, and from `api.anthropic.com` when an Anthropic API key is set.

## Trust

The user's own `~/.claude/model-router.json` is trusted in full. A repository's `.claude/model-router.json` is read like any project file. It can turn routing down to `advise` or `off`, set rules for agents, and add models. It can't switch routing on for a user who turned it off, and it can't change the strength of a model that ships or that the user defined. A name it adds that sits inside one of those names, or contains one, is dropped, and the names it adds are matched against a model ID last, so a name like `claude` can't claim the shipped models' IDs. Models it adds are ignored while the user has a cap set, since their rank is only the repository's claim.

## What is out of reach, or left alone on purpose

- The main conversation's model, by choice (see above).
- Forks. A fork reads its parent's cache and always runs on its parent's model.
- Agents with no definition on disk that the engine didn't make (command-line agents), unless `agent_pins` is `override`.
- Answer quality. The profile is an estimate from the brief's wording and the agent's name. Redo escalation and the feedback loop react to what Claude redoes. Neither measures whether an answer was right.
- Sessions where hooks are disabled by `disableAllHooks` or `allowManagedHooksOnly`. Where an organization doesn't load user mods, routing falls back to the `Agent` call and workflow stages and teammates aren't routed.
- Another installed mod that also sets a spawn's model. Mods run in their own order. The model that ran is in the ledger either way.

## Verified live

On Claude Code 2.1.330, with the plugin loaded from disk, in headless sessions and in an interactive terminal session in auto mode:

- A Sonnet session started a lookup and a security review in one message. The mod sent the lookup to Haiku at low effort and the review to Opus at xhigh, and raised the main turn's effort for the hard request. Every turn's spend was logged.
- A workflow script's one stage, started from a Sonnet session, ran on Haiku at low effort.
- Per-request effort: a complex request raised the main loop from medium to high on each of its requests, and a Haiku lookup subagent ran at low on each of its own.
- With `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` pinning Opus, a lookup subagent stayed on Opus at low effort, and the session-start note said why.
- An agent that pins its model kept it and took its own frontmatter effort.
- In the interactive auto-mode session, no spawn was denied, and each subagent's first request already carried its routed effort.
- Three copies of one lookup brief, started in one message and run in the background, all ran on Haiku at low effort. The same call re-issued after its background run had finished ran on Sonnet at high.

On the `Agent` call path: a Sonnet session in default and interactive auto mode routed a lookup to Haiku and a security review to Opus; another installed plugin's agents were routed the same way; a cap of Sonnet lowered a Fable agent; a rejected rewrite ran untouched on the retry; and on 2.1.270 the rewrite applied and the agent ran on Haiku.
