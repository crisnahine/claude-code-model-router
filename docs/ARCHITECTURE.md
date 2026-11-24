<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><img src="assets/banner-architecture.svg" width="880" alt="Architecture: How do two paths share one router?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Next: <a href="../GLOSSARY.md">Glossary</a></p>
<!-- End of page head. -->

# Architecture

## The idea

Model Router sets the model and reasoning effort of every agent Claude Code starts: subagents from any plugin, skill or agent, teammates, and workflow stages. With the `main_effort` option on, it also raises the main conversation's effort for a hard request. The agent the caller chose, its prompt and its tools stay as they were.

The aim is the lowest cost per finished task at a high quality bar. Every subtask starts on the cheapest model and effort that should do it, and moves up only when its result comes back for a redo. Anthropic's own measurements back that order: running at low effort and re-running failures at high solved about 97% of a coding benchmark for $0.17 a task, against 95.3% for $0.29 with every task at high ([docs/research/model-control.md](research/model-control.md), Q7).

## Two control paths, one router

![Two control paths, one router: Claude Code engine events reach the mod, hooks/router.ts, and the settings hooks in hooks/hooks.json are the fallback and do the bookkeeping. Both run sh model-router.sh, which calls one Python runtime that decides every spawn.](assets/two-paths.svg)

The mod (`hooks/router.ts`, loaded from `hooks/hooks.json` under `modules`) is the primary path. It hooks eight engine events and asks the Python runtime through four `mod-*` commands, each payload with `session_id`:

| Event | Command and payload keys | What the mod does |
| :- | :- | :- |
| `tool.call` (`Agent`) | none | Keeps the `effort` Claude put on the call, by `tool_use_id`, since `agent.spawn` has no effort field. |
| `session.start` | `mod-start`: `settings` | Marks the session as routed by the mod and sends the settings layers it read. |
| `agent.spawn` | `mod-spawn`: `settings`, `cwd`, `parent_agent_id`, `parent_model`, `session_effort`, `kind`, `tool_use_id`, `tool_input` | Fires for every subagent, teammate and workflow agent before its model resolves, and passes the pick on as `model`. `parentModel` is the parent's exact effective model. `kind` is `subagent`, `teammate` or `workflow`. `tool_use_id` is left out for a workflow's agents, which share one. Forks and resumed runs are left alone. |
| `prompt.submit` | none | Keeps where an idle prompt came from (`origin.kind`), and a typed `/skill`'s name and arguments. |
| `skill.prompt` | none | Keeps the body a typed `/skill` renders. |
| `turn.start` | `mod-turn`: `settings`, `text`, `model`, `first_party`, `origin`, `command` | For the main loop, asks for the turn's effort. A typed `/skill` turn's text is a `<command-message>` line, so the command's name, arguments and body go with it. An agent turn that starts before the spawn hook has the agent's id is matched by its prompt, and takes the effort its waiting spawns got, only while they all got the same one. |
| `turn.step` | none | Applies the agent's routed effort, or the main loop's turn effort when that is a raise, to each model request. Remembers the main loop's effort as `session_effort`, and keeps each request's usage. |
| `turn.complete` | `mod-usage`: `agent_id`, `subagent_type` (workflow agents), `usage`, `requests` | Writes the turn's usage and cost to the ledger. Each request is priced on its own, because Haiku 5.5 bills a prompt over 100,000 tokens at a higher rate (https://platform.claude.com/docs/en/about-claude/pricing.md, Long context pricing). A turn without its requests is priced at the base rate. |

The mod runs the launcher the settings hooks use, through `$.process.run` with the plugin's hook environment and an 8-second timeout, so there is one decision procedure and one state store. A `mod-spawn` round trip takes about 58 ms warm and 187 ms cold (p50), almost all of it starting Python ([Performance](PERFORMANCE.md#mod-path)).

The settings hooks do every job the mod doesn't: the session-start note, the request profile, availability tracking from each `Agent` result, redo bookkeeping, and the two checks before a turn ends. `PreToolUse` on `Agent` is the fallback path: it rewrites the call's `model` and `effort` with `updatedInput`, and stands down once any mod command has marked the session (`mod.active`), so a call is never routed twice. It covers Claude Code versions without mods and organizations that don't load user mods.

The mod comes first for three reasons. It reaches teammates and workflow stages, which the `Agent` call doesn't carry. It sets the model after auto mode reviewed the call, so auto mode's "input changed after review" denial doesn't apply. And it can set effort on each request, the only lever a forced subagent model leaves.

## Components

| Module | Job |
| :- | :- |
| `config` | Builds the `Registry` from `config/models.json` and the two override files (`config.load`); holds the `Level` type |
| `registry` | The `Registry` every step reads, with `available`, `select`, `clamp`, `family`, `newest_id` and `cost` |
| `profile` | Reads a brief (`read`), scores it as a call or a request (`level`), and maps agent names to roles (`ROLES`, `role`) |
| `ladder` | A level, the priority and learned raises give the pick: a model and an effort |
| `spawn` | The procedure both paths call: `decide`, then `record` |
| `router` | The order of authority: one decision from the facts it is given alone |
| `fallback` | The `Agent` call path's rewrite, its waiting record, the refused-rewrite guard and the one deny |
| `mod` | The mod path's four commands, `active`, and the pure `turn_effort(TurnFacts, registry)` |
| `intake` | Whether a prompt or turn is the person's request, and its level |
| `request` | The current request: level and floor, the model the user named (`named_model`), start time, the escalation asked, code changed, and `Request.below` |
| `calls` | A `Route` per spawn, the request's strongest spawn (`strongest`), and the one rule set for what comes back (`returned`) |
| `learn` | Counts routed spawns, casts votes, raises levels (`spawned`); a swapped run takes its count back (`swapped`) |
| `health` | Model errors, substitutions and refused rewrites in; healthy or routed around, and last ran as, out |
| `agents` | An agent's definition: the project, the user folder, this plugin, another installed plugin, or a built-in |
| `session` | The model the conversation runs on |
| `gates` | The two checks before a turn ends |
| `upstream` | Releases, the docs index, aliases and model status against what was last reviewed |
| `settings` | Options, environment facts, and the `Snapshot` each hook call builds once. A read writes nothing; a spawn writes what reads noticed (`Snapshot.settle`) |
| `allowlist` | The models `availableModels` and `deniedModels` leave open, and each model's `maxEffortLevel` cap |
| `ledger` | One writer per row kind, the turn cost rule (`turn_cost`), and reading rows |
| `hooks`, `cli`, `report`, `store` | Settings-hook handlers and the command map (`hooks.HANDLERS`); the command line; tables and `--json`; state files and the ledger |

All of it is standard-library Python, plus the one TypeScript file the engine runs. No module names a model: the models, and aliases such as `best`, are entries in `config/models.json`.

## The ladder

| Level | Needs | Model (balanced) | Effort |
| :- | -: | :- | :- |
| lookup | 1 | Haiku | medium |
| standard | 2 | Sonnet | medium |
| complex | 3 | Opus | high |
| hardest | 3 | Opus | xhigh |
| frontier | 4 | Fable | high |

![The ladder: lookup on Haiku at medium effort, standard on Sonnet at medium, complex on Opus at high, hardest on Opus at xhigh, frontier on Fable at high. A call climbs one rung each time it comes back for a redo.](assets/ladder.svg)

The profile scores each subtask from its own brief. It looks for four signals: risk (security, data and its consistency, migrations, public APIs), scope (several modules or services), load (performance, latency and capacity limits, counted only when the brief also asks for a design or a choice) and ambiguity (design, a choice between approaches, root cause, trade-offs). It also checks whether the subtask changes or reasons about something or only reads.

The agent's name counts too, by its whole parts split on `-`, `_` and `:`. A heavy name such as `architecture-advisor` or `cybersecurity-analyst` sets a complex floor. A search or check name such as `docs-researcher` or `lint-runner` caps the brief at standard, while `researcher` and `information-extractor` are neither. `docs` counts only before another part, so `docs-lookup` is a search agent and `update-docs` isn't.

A brief whose work is to fix or hunt a defect is at least complex, however small: Claude Code's effort guide puts "work where verification matters or edge cases are likely, such as fixing a bug in an existing codebase" at `high` (https://code.claude.com/docs/en/model-config.md, choose an effort level), and the research notes pick Opus for risky, judgment-heavy work (`docs/research/quality-first-routing.md`). `fix` and `debug` count only as verbs ("the fix", "fixture" and "debug logging" don't), and a fix the brief rules out ("do not fix", "out of scope") doesn't count. "Find why" and "find" before a defect noun ("find the race condition") hunt a defect, while "find the regression tests" stays a lookup. The request profile (`profile_prompt`) applies the same rule to the person's request, except that a hunt verb such as "analyze" or "investigate" counts only beside a defect word such as "bug", "failing" or "slow", since people "analyze the sales numbers" too.

Calls and requests are read the same way (`profile.read`) and scored by `profile.level`, with their own rules on purpose:

| | A call | A request |
| :- | :- | :- |
| Asks for no change or reasoning | a lookup (standard with two signals) | still scores from its signals |
| Long text | over 4,000 characters lifts a lookup to standard | over 1,500 characters adds scope |
| A negated limit ("do not change any files") | set aside | counts as written |

Each step comes from the measurements in the research notes:

- Complex is Opus at `high`. On a SWE-bench Pro subset Opus 5.5 at `high` solved 95.3% at $0.29 per solved task, against 92.8% at $0.22 for `medium` and 87.6% at $0.11 for Sonnet 5.5 at `high`. "The bill is decided by the tasks the cheaper model fails" (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence; `docs/research/quality-first-routing.md`). A migration or a performance optimization "can have clear specs and still be difficult enough to warrant Opus 5.5" (https://claude.dev/blog/which-claude-model-should-you-build-with/, a blog post), and those are the risk and load briefs that reach complex. An earlier ladder (standard at `high`, complex on Opus, hardest at `xhigh`) cost 1.2 to 2.2 times as much per solved task as Claude Code's defaults on 7 of 8 task classes on a Sonnet session, at a pass rate no lower; those rows are in `bench/results/archive/`, outside [BENCHMARKS.md](BENCHMARKS.md). That benchmark can't show what a stronger model buys on hard work: <!-- bench:opus.saturated -->5<!-- /bench --> of <!-- bench:opus.classes -->8<!-- /bench --> Opus classes are saturated, so it makes no quality claim for them.
- Standard is Sonnet 5.5 at `medium`, Claude Code's own default for it, for work with a clear spec. No rung runs Sonnet above `medium`: a defect fix or a risky change goes to complex.
- Hardest is complex's model at more effort, Opus at `xhigh`. On 463 of the subset's problems Opus 5.5 solved 95.5% at `high` for $0.30 per solved task and 96.5% at `xhigh` for $0.75, against 96.3% for Fable 5.2 at `high` for $1.39 (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence). That last point costs two and a half times as much, so only the hardest work pays it. The headline figures in BENCHMARKS.md predate this ladder: that run had complex on Sonnet.
- Frontier is Fable, for work that is hardest and also open-ended or research-grade, and for redos of hardest work.

A level's `needs` is a strength, finite and never below the level beneath it, so a raise to one level lifts the levels above it (`config._levels` keeps a running maximum). `ladder.pick` adds any learned raise, and `Registry.select` takes the cheapest usable model at or above it, so a cap or an unavailable model moves the pick without any level naming a model. `quality` priority goes one step stronger, and `fast` one step lighter, taking the quickest model that qualifies. `fast` also sets no effort above `low` (`ladder.effort`), so on the shipped ladder it sets none.

When the pick is weaker than the level's `needs` (a cap, the allowlist or model health left nothing at or above it), it gets one more effort step, at most `xhigh` (`ladder.effort_on`): with Opus out of reach, complex's `high` becomes `xhigh` on the model left. That narrows the gap at most. The docs give no Sonnet figure at `xhigh`, and Sonnet 5.5 at `xhigh` can start its own rounds of review, which costs time and tokens (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence). Only the ladder's pick gets the step; a pin, a model named on the call or a rule that `Registry.clamp` lowers keeps the level's own effort.

## The decision

Both paths call one procedure in two steps. `spawn.decide` gathers the facts (the agent's definition, the model it would inherit, the usable models, this call's finished run earlier in the request, the learned raises) and passes them to `router.decide`. It writes nothing. `spawn.record` writes the ledger row (`via` names the path), the spawn's route in `calls`, and its count and votes in `learn`, each under its own file's lock, so one failed write doesn't cost the others. The fallback adjusts the decision between the two steps, so the ledger row shows the guarded result. The fallback turns the decision into `updatedInput` and a note, the mod into `{model, effort, reason}`.

`router.decide` reads no state: everything arrives as facts. It returns keep or rewrite, the model, effort, level and a reason, which the ledger keeps so every choice can be explained. The `explain` command and `/model-router:route` run `spawn.decide` on a text, so they report what a real call would get, rules, pins, a redo and the request's floor included, and write nothing. Without `--agent` they also read the text as the user's own request.

Order of authority, strongest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`: the user pinned every subagent's model. The router sets effort only.
2. `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`, for a workflow agent with no model named on it. It wins over the model a mod sets (a live run on 2.1.331), so the router sets effort only.
3. A model named on the call: by Claude, the user through Claude, a skill or a workflow script.
4. A per-agent rule in a `model-router.json` file. The user's file decides in full. A project's file only raises: its `level` is a floor, and its `model` applies only when it isn't weaker than what the call would run on without it (the pick, the agent's pin, `CLAUDE_CODE_SUBAGENT_MODEL`, or the inherited model). Otherwise that stands, and the decision's `held` field and the ledger say so. The suggestion is also held when that model isn't known, as for an agent with no definition found, and when the project's own file added it. A project's `route: false` is ignored.
5. The agent's own `model`, unless `agent_pins` is `override`.
6. `CLAUDE_CODE_SUBAGENT_MODEL`, the user's default for agents without a model, also unless `agent_pins` is `override`.
7. The router's pick: `ladder.pick(level, priority, usable models)`.

Then `Registry.clamp` moves the result to the closest usable model at or below it, or to the weakest usable model when none is below, so it raises only when everything usable is stronger.

The scout, verifier and docs-researcher pin Haiku at `medium` in their own definitions, so step 5 keeps them, also where the router doesn't route. At `low` the model is more likely to skip a search, stop early or skip a check in a long agent prompt (https://platform.claude.com/docs/en/build-with-claude/effort), which matters most for the verifier, the check the cheap-first ladder rests on. The implementer, architect, reviewer and deep-solver name no model or effort, so step 7 routes them by their brief, or step 6 when `CLAUDE_CODE_SUBAGENT_MODEL` is set. Where the router doesn't route, they run on `CLAUDE_CODE_SUBAGENT_MODEL` if set, else on the conversation's model (https://code.claude.com/docs/en/sub-agents.md#choose-a-model).

Role names set floors (`profile.ROLES`): complex for the architect and reviewer, hardest for this plugin's `model-router:deep-solver` only, so `sudoku-solver` gets complex and `resolver` none. The hard-request note (`UserPromptSubmit`) and the `Stop` check name a strong agent, the architect or for a frontier request the deep-solver, by name only (`gates.strong_agent`). A call to the architect, reviewer or deep-solver starts no lower than the request's level (`spawn.facts`), since a hand-off brief often reads simpler than the request; the floor is off while Claude handles another session's or a teammate's message. A redo still climbs from there, which a model named on the call would stop at step 3.

Effort is set when the router, a rule or the agent's pin chose the model and nobody chose an effort: not the call, the agent's definition, `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` (`router._effort_decided`). `CLAUDE_CODE_SUBAGENT_EFFORT` "doesn't apply to forks, forked skills, or teammates" (https://code.claude.com/docs/en/env-vars.md), so the router sets a teammate's effort anyway. The mod knows a teammate by the spawn's `kind`. On the fallback, a call from the main conversation with a `name` and no `isolation` launches as a teammate when agent teams are on (https://code.claude.com/docs/en/sub-agents.md#subagent-names), so the router treats it as one (`router.Facts.teammate`). The hook can't see whether agent teams are on, so such a call counts as a teammate anyway, for votes too: it counts toward nothing and casts none.

On the session's own model, an agent with no other effort runs at the session's level (https://code.claude.com/docs/en/model-config.md#effort-level-of-subagents-and-teammates), such as `xhigh` under ultracode. So for a call from the main conversation, an effort the router sets on that model is raised to the session's level when that is higher (`router._session_floor`), after the redo step and before the cap, on the two pinned branches too. The mod sends that level as `session_effort`; the fallback reads the hook input's `effort.level`. A nested call gets no floor, since it runs at its parent's effort. The ledger records the raised effort, so a redo asks for more than the run it replaces.

Every effort the router sets is then held to `maxEffortLevel` for the model that runs (`router.capped`, `allowlist.Allowlist.effort_cap`). The lowest cap of any source applies, a source's `modelSettings` entry for a model replaces that source's top-level cap, and `max` sets none (https://code.claude.com/docs/en/settings-reference.md#maxeffortlevel). `modelSettings` is keyed by model ID; several IDs of one family read as the highest of their caps. Claude Code applies the cap anyway; doing it here keeps the ledger and `/model-router:route` true to what runs. The reason then says "effort held to <cap> by maxEffortLevel", as does a main-loop turn row's `reason`.

Some details of how the rules apply:

- A call that would run on the same model anyway, with no effort to set, is kept as it is.
- An agent whose definition can't be found is left alone, since it may have a model its author chose. Workflow stages and teammate roles are the exception: the engine makes them, and they name no model unless the script or the call does.
- On the fallback, inside a subagent and for the built-in Explore and Plan agents, the inherited model isn't known for certain, so the pick is always written onto the call.
- A call that names a model outside the registry, such as `inherit` or a gateway's own ID, is left as it is.
- A model the user names in a prompt (`request.named_model`) reaches Claude as a reminder to put it on that request's calls; "opus" is an ordinary word too, so the hook forces nothing. A call on that model casts no feedback votes.
- Where a provider's alias for the family resolves to an older model (`settings.provider`, `older_aliases`), the mod sends the family's newest ID instead (`Registry.newest_id`), except under an `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` pin, `modelOverrides`, allow or deny lists that rule the version out (`allowlist.Allowlist.permits_id`), an exact version named by the caller or agent, or Mantle, whose aliases aren't documented. One model error on that ID routes around the ID alone. The route keeps the ID sent (`sent`), and a failure reads its own spawn's route, so copies failing together on the ID never strike the alias. Only `agent.spawn` takes a full ID; the `Agent` tool's `model` takes an alias (https://code.claude.com/docs/en/sub-agents.md). [Guide](GUIDE.md#third-party-providers) lists the providers.

### Redo escalation

![Redo escalation: a call routed at standard comes back, the same call issued again runs at complex, and the redo counts as a vote that standard was too low. At least 6 votes that are also at least 30% of the level's calls raise the level one step.](assets/redo-escalation.svg)

Every spawn that isn't denied writes a route (`routes`, per session): its level, model, effort and who chose it. When a result comes back, one rule set (`calls.returned`) reads that route, whichever path wrote it. A result finishes the call when it isn't a background launch, didn't swap models and wasn't cut off by an API error, and the call is then kept with its level (`done`) when the router, a pin or a rule chose the model, or the router rewrote the call. A background agent's call returns at launch, so it waits under the agent's id (`launched`) and finishes when the agent stops with an answer: a closing message, or a report handed back through `SubagentHandback` (https://code.claude.com/docs/en/hooks.md#subagentstop). So copies of one brief started together all run at the brief's own level. `post-agent` runs before Claude's next step, so a redo always finds the record.

The same call (agent and prompt, `calls.identify`) issued again in the same request (the same prompt id, or within 30 minutes without one) means the first answer wasn't good enough, so it runs one level higher. On the shipped ladder, lookup to standard, standard to complex and hardest to frontier change model, and complex to hardest stays on Opus at `xhigh`. A redo on the model of the run it replaces runs at least one effort step above that run (`router._above_earlier`), unless the call, the agent's definition or the user's environment chose the effort. If the redo would still repeat that run's model and effort, because `maxEffortLevel` holds the step or the chosen effort is the same, a router-picked model moves to the next stronger usable model, and the reason says so; a rule's model and an agent's pin stay. Each route records the effort the call runs at, in Claude Code's order (https://code.claude.com/docs/en/sub-agents.md#choose-an-effort-level): `CLAUDE_CODE_EFFORT_LEVEL`, the call's, the agent's, the router's, then `CLAUDE_CODE_SUBAGENT_EFFORT` (`router.runs_at`). A call that names its own model is never escalated.

Claude usually rewrites a brief it sends again, so the same call rarely comes back word for word. A call whose `description` starts with "Redo:" (`calls.marked_redo`) is a redo whatever its prompt says, and the session-start note asks Claude to keep the description and put that mark in front. The router finds the run it redoes by its agent and its description without the mark (`calls.task`, `calls.last_run`), within 30 minutes and in any request, since a redo often follows the user turning a result down, and runs the call one level above it. With no such run on record, the call redoes the run its own brief would get without the mark (`spawn._unmarked_run`), so a cap, a pin or the session's floor can't leave it on the same model and effort. That redo casts no vote, since nothing is known to have run at the lower level. No hook input marks a call as a retry, and a key outside the Agent tool's schema is dropped before hooks see it, so `description` is the one field that can carry the mark ([research](research/caller-levels-and-redo.md), Q3).

Three limits hold for every redo. A workflow stage is never redone, marked or not (`spawn.facts`): its script decides what runs again, and a fan-out needs one model and effort. A redo of a frontier run stays at frontier and asks for more effort. Where `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` pins the model, a redo asks for more effort than the run it replaces. The mark is the caller's own statement, so a marked call climbs even when the run before it was cut off by an API error.

The redo is also a vote that its first level was too low, but only when the router chose the model: a pinned agent's or a rule's redo climbs and casts none. Votes are measured against the level's count of spawns (`learn.counted`), which holds only subagents the router chose a model for, so a hundred workflow stages leave the threshold at its floor of 6. A level is raised one step, at most (`learn.MAX_BUMP`), once its votes reach 6 and 30% of its counted calls.

A run whose model changed mid-run is its own result. Claude Code reports it as `modelsUsed` on a finished foreground `Agent` result (https://code.claude.com/docs/en/hooks.md); a content-classifier fallback can cause it, such as Sonnet 5.5 re-run on Sonnet 5 (https://code.claude.com/docs/en/model-config.md, "Automatic model fallback"). Sonnet 5 scored 10.3% against 70.6% on Terminal-Bench 4.0 (https://www.anthropic.com/news/claude-sonnet-5-5), so such a run says nothing about the level or model asked for. It isn't health evidence, doesn't finish the call, and takes back its route's count (`Route.counted`, `learn.swapped`). Its result row carries `models_used`, and when the brief had a risk signal Claude is told to check the result.

A run whose whole answer is an API error such as an overload (`calls.cut_off`) doesn't finish the call either: sending it again is a retry. Its result row has `status: cut_off`.

### Rewrites that don't take

On the fallback, Claude Code reports no event when the `Agent` tool rejects a rewritten call, including auto mode's denial of input a hook changed after review (https://code.claude.com/docs/en/hooks.md#pretooluse-decision-control), and rewriting the retry would loop. So every rewrite waits in `pending` until its result or failure. If the same call shows up first, 5 seconds or more later (`fallback.REFUSED_AFTER`), the rewrite didn't run, and the call passes through untouched. Copies issued in one message arrive together, so each is routed as usual. The guard lives in `fallback.py` alone. A refused rewrite to a model that has never run here is a strike against it, and two strikes route around it.

One case can't pass through: if the rewrite lowered the model and the call's untouched model is above the user's cap, the repeat is denied, with the model to use in the reason. This is the plugin's only denial, and the mod path never needs it.

### Permissions

Neither path approves anything. The hook returns `updatedInput` and a one-line note, never `permissionDecision: "allow"`, and the mod's spawn hook only passes a model on. The user's ask rules and auto mode's review of the spawn apply as they would without the plugin.

## The conversation's own model

A mod could send the main loop's requests to another model, and the first probe of this design did. The router doesn't, by choice: each model keeps its own prompt cache, so a switch re-reads the whole conversation uncached, and switching back does it again. An effort change keeps the cache on current models with an API key or a subscription, so the main loop gets effort instead.

`turn.start` profiles the request. With the `main_effort` option on (off by default), a complex, hardest or frontier request raises that turn's effort to `main.effort` in the registry (`high`, `xhigh`, `xhigh`), held to any `maxEffortLevel` cap. `turn.step` applies it only above what the session already asks for, so `/effort`, `ultrathink` and the user's settings win. `mod.turn_effort` decides from `TurnFacts` alone, so every case is a row in its table test. A turn at complex or above that gets no effort writes a `turn` ledger row with `effort: null` and the first reason that applied; under routing `off` no turn row is written.

The raise is skipped wherever an effort change drops the cache (https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level), or the router can't confirm it survives:

| Skipped when | Why |
| :- | :- |
| Routing isn't `auto`, `main_effort` is off, or `CLAUDE_CODE_EFFORT_LEVEL` is set | The user decided |
| `$.session.authorize()` finds no first-party credential | A Claude apps gateway or a third-party provider. A null in the desktop app, which never lends its sign-in to plugins, counts as unknown and is raised |
| Bedrock (Mantle included) or Vertex | The cache drops |
| Foundry, or `ANTHROPIC_BASE_URL` | The docs don't list Foundry, and a gateway decides what stays cached |
| `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` | The cache drops |
| `DISABLE_PROMPT_CACHING`, or its variant for the session model's family (https://code.claude.com/docs/en/env-vars.md) | No cache to keep |
| The session's model is known and not under `main.effort_keeps_cache` (Fable 5.1 or later, Opus 5.5, Sonnet 5.5, Haiku 5.5) | The cache drops |

An unknown model, as after `/clear` when SessionStart sends none, is raised. The model is the mod's `$.session.model()` when that is a model ID, else what SessionStart or PostModelSwitch recorded.

`intake.py` decides, for both paths, whether a turn is the person's request, so a delivered message never raises effort, replaces the request's profile or adds a note. Not the person's: text framed as "Another Claude session sent a message:" over a `<teammate-message>` or `<cross-session-message>`; text that opens with a framing tag Claude Code adds (`<task-notification>`, `<command-...>`, `<bash-...>`, `<local-command-...>`, `<system-reminder>`); and text that is nothing but elements with hyphenated tag names, as every framing seen so far has. A request that opens with its own tag (`<button> does nothing on Safari`) or is written in XML tags (`<task>...</task>`) is the person's. The mod also skips five `prompt.submit` origins (`task-notification`, `peer`, `peer-send-message`, `observer`, `observer-activity`); every other kind, a new one included, is the person's (`intake.ORIGINS`).

A skill typed as `/name` is profiled by what it asks for (`intake.profile_skill`). When its content takes the arguments through a placeholder (`$ARGUMENTS`, `$1` or a declared `arguments` name, https://code.claude.com/docs/en/skills.md#available-string-substitutions) and the arguments say what to do (a change, reasoning or lookup verb plus another word, once paths, numbers, flags and URLs are set aside: `profile.asks_for_work`), the arguments alone set the level. So `/model-router:ship fix a typo` routes as that fix. Arguments that only name targets (`482`, `src/a.py`, `the payments service`) leave the task to the body, held at most complex, since a template body serves any target. When nothing takes them, the engine appends them as `ARGUMENTS: ...`, and the higher of the arguments' level and that of the body's first 1,500 characters counts, as for a skill typed bare. A body's length never reads as scope.

The mod gets the body from `prompt.submit`'s `command` and the `skill.prompt` text, and sends it only to a turn that opens with that command's `<command-message>` line. `UserPromptSubmit` sees only the typed line (https://code.claude.com/docs/en/hooks.md#userpromptsubmit-input), so it reads the raw `SKILL.md` or command file from the project or user folder, a regular file of at most 64 KB, and finds placeholders itself (an unescaped `$ARGUMENTS`, `$N` or `$ARGUMENTS[N]` with an argument at that index, or a declared name the body uses; `$500` is prose). A plugin's skill is read from `skills/<name>/SKILL.md` under this plugin's root or a root in Claude Code's install record (`agents.installed_roots`, which walks nothing, since this runs on every typed line). One the record doesn't list, such as a `--plugin-dir` plugin, is profiled by its arguments alone. The paths can disagree where the engine's substitution differs from this reading, such as quoted arguments. A `/skill` typed while a turn runs is queued, and the mod doesn't profile it yet.

When the readings differ, the request keeps the higher. `UserPromptSubmit` starts the request (`request.start`). The mod's `turn.start` raises its level (`request.raise_to`) for the person's typed command, under any routing mode but `off` and whatever `main_effort` says, so a command the settings hook couldn't read still gets the strong-agent floor and the Stop check. It never lowers the level, starts a request, or raises from plain turn text, which may carry other hooks' context.

When the request needs more than the session's model (`Request.below`), the `UserPromptSubmit` note says a strong subagent can give the plan or decision when a few reads of the code can't settle it, and that the conversation works directly otherwise. For the architect it adds that the agent writes nothing. A normal prompt gets no note, so it costs no extra tokens.

The two `Stop` checks ([Guide](GUIDE.md#the-checks-before-a-turn-ends)) each ask once. `strong_agent_gate` blocks when a complex or harder request on a weaker session ended with no agent of the needed strength started (`calls.strongest`). It asks for the reviewer, given a saved diff, when code changed during the request, else the architect, or the deep-solver for a frontier request; the reviewer starts no lower than the request's level too. `verify_gate` asks when code in the project (under `cwd` or `CLAUDE_PROJECT_DIR`, prose files such as `.md` and `.txt` aside) changed and, after the last edit, no check command ran and no verifier or reviewer is still running. A command is split at `&&`, `||`, `;`, `|` and newlines, even inside quotes, and a part counts when it names a test, lint, type-check or build tool and doesn't start with a word that only installs, finds, reads or mentions one (`gates.is_check`): `cd api && pytest` counts, `pip install pytest` doesn't. A subagent's commands count too, read from the transcript `SubagentStop` names (`agent_transcript_path`).

Neither check runs when routing isn't `auto`, on a stop the hook already blocked (`stop_hook_active`), or while a workflow or subagent runs in the background (`gates.still_running`), since the turn is only paused. The verify check also waits while a verifier or reviewer runs in the background.

## Discovery and adaptation

| What | How it is discovered | What happens |
| :- | :- | :- |
| The models that exist | `config/models.json`, plus the user's and a project's additions | They are the candidates |
| New versions of a model | Nothing to discover: routing uses aliases, and Claude Code moves each to the newest version. Where a provider's alias lags (`older_aliases`, from https://code.claude.com/docs/en/model-config.md), the mod sends the newest ID from `newest` | Picked up the day Claude Code ships them; on a lagging provider, when a release updates `newest` |
| A setup running behind the newest version | The session's model and what each alias resolved to, against the newest active version in the model status data | Named at session start |
| The models you allow | The `max_tier` option | Stronger ones are never picked, and pins and explicit choices are lowered |
| The models your organization allows | `availableModels`, `deniedModels` and `availableModelsMatch`, read as Claude Code reads them, from the settings layers the mod sends, else the settings files; the managed files always apply as a floor ([Guide](GUIDE.md#organization-allowlists)) | Blocked ones are not picked. When they leave no registry model open, the call is left as it is and the ledger says why |
| The model a name resolves to | `resolvedModel` in the `Agent` result, and the model on each turn's usage | Shown in `/model-router:models` and `/model-router:usage` |
| A model that isn't served here | A model error from the `Agent` tool, twice | Routed around for a day, then retried; trouble after a retry waits three days, then seven. More failures in one outage don't stack, and a clock moved back can't stretch a wait |
| A model that is silently replaced | The routed or named model against `resolvedModel`, twice, except under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` | Same |
| A model name the `Agent` tool rejects | A rewrite to it comes back without having run, twice | Same |
| A level routed too low | A redo of a routed call, or Claude naming a stronger model than the pick | Redos run one level up at once. Enough votes raise the level one step; a raise that keeps being overruled with weaker models is undone |
| A new or removed model alias | The docs' model alias table, daily | Reported by `/model-router:refresh`, and at session start for a new alias; the repo's daily workflow opens an issue |
| A model being deprecated or retired | The platform's "Model status" table, and the Models API with an API key, daily | Reported the same way: at session start with its retirement date for a model routed calls ran on, and by `/model-router:refresh` with its replacement |
| A Claude Code release, or a change to a docs page this plugin relies on | Changelog RSS and `llms.txt`, daily | Reported the same way |
| A model with no price | A turn's usage names a model the `prices` table lacks | Listed by `/model-router:usage` |

A new model family is reported but not routed to until it has a strength rating, since a guess could send it work it is too weak or too costly for. Feedback never takes a level below what ships.

With `ANTHROPIC_API_KEY` set, `ANTHROPIC_BASE_URL` unset and none of `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or `CLAUDE_CODE_USE_FOUNDRY` on, the daily check also reads the Models API (`GET /v1/models`): its `line`, `lifecycle` and `retires_at` take precedence over the public page, and a new `line` is reported as a new family. The key goes to `api.anthropic.com` alone, with no redirect followed. A subscription login gives hooks no key.

An empty changelog or docs index (an outage page), a body cut short of its `Content-Length`, or a fetch past its limits (10 seconds for a body, 25 for the whole fetch) counts as a failed fetch: the last cache stays, and the repo's daily workflow fails instead of opening an issue. Claude Code doesn't time out an async hook ([hooks docs](https://code.claude.com/docs/en/hooks.md#run-hooks-in-the-background)), so these limits end a stuck refresh. After a failure, session starts wait an hour, doubling with each failure in a row up to a day; a success resets the wait, and `/model-router:refresh` always tries. [Security](SECURITY.md#network) has the hosts and limits.

## Hook flow

The mod's events are in the table under [Two control paths](#two-control-paths-one-router). The settings hooks:

```text
SessionStart ─┬─ session-start   records options and the session model; explains routing
              └─ refresh-cache   (async) refreshes the upstream cache if older than 24 h,
                                 waiting 1 h to a day after a failed fetch
UserPromptSubmit ─ user-prompt   profiles the request; notes a model the user may be asking for
PostModelSwitch ─ model-switch   records the session's new model
PreToolUse (Agent|Task) ─ pre-agent      fallback: fallback.route, returns updatedInput
PostToolUse (Agent|Task) ─ post-agent    the model that ran; a finished result is kept for redos
SubagentStop ─ agent-stopped    a background agent's result or handback is back; counts its checks
PostToolUseFailure (Agent|Task) ─ agent-failed   counts model errors
PostToolUse (Edit|Write|NotebookEdit) ─ post-edit   notes that code changed
Stop ─ stop                      the two checks
```

`cli.py` catches whatever a handler raises and exits 0, and every mod hook has a `.catch` that passes the event on unchanged. The launcher exits 0 when Python is missing and turns any other hook failure into exit 1, since exit 2 would block the tool call, the prompt or the stop (https://code.claude.com/docs/en/hooks.md#exit-code-2-behavior-per-event). A bug here can cost routing, never block a session.

## State

In `${CLAUDE_PLUGIN_DATA}` (without it, `~/.claude/plugins/data/model-router-local`):

| File | Contents |
| :- | :- |
| `state.json` | Every plugin option as SessionStart recorded it, for skill commands, which run without them; and model health |
| `upstream.json` | The upstream cache and what was last reviewed |
| `allowlist.json` | Per session: each settings source's allowlist keys and `maxEffortLevel` caps, as the mod's hooks last sent them. A source whose read failed keeps its last set, else its file is read |
| `learned.json` | Calls and votes per level, and which levels are raised |
| `locks.json` | Per state file: lock waits, waits on another session's lock, skipped writes, stale locks broken, and when the last happened |
| `sessions.json`, `prompts.json`, `routes.json`, `edits.json`, `pending.json`, `launched.json`, `done.json`, `mod.json` | Per session: its model, the request's record, each spawn's route, edit and check times, rewrites waiting for a result, background agents still running, finished calls for redos, and whether the mod is running. Each file keeps the 200 most recently written sessions, and each session's per-call records keep the 100 newest. `routes`, `launched` and `done` also drop a session a day after its last write (`calls.KEPT_FOR`), since every spawn rewrites them whole under one lock |
| `ledger.jsonl` | One JSON line per event, below. Over 4 MB, its older half is dropped |

Every ledger row has `ts`, `event` and `session`:

| `event` | Fields |
| :- | :- |
| `spawn` | `via`, `agent`, `action`, `level`, `signals`, `model`, `was`, `effort`, `source`, `escalated_from`, `reason`, `description` (first 120 characters); `kind` on the mod path; `held` when a project rule's model was held |
| `result` | `agent`, `asked`, `resolved_model`, `status` (`cut_off` for an API-error answer), `total_tokens`; `models_used` only on a mid-run swap |
| `model_error` | `agent`, `model` |
| `turn` | `level`, `effort` (null when a complex or harder turn got none), `command` for a typed skill, `reason` |
| `usage` | `agent_id` (none for the main loop), `model`, the four token counts, `ephemeral_1h_input_tokens` when reported, `cost_usd` at list price (null when unpriced); `agent` for a workflow agent, which has no result row |

The request's strongest spawn isn't stored: `calls.strongest` reads it from the routes written since the request started (its `prompt_id`, else its `started` time). A request with more than 100 spawns can lose its strongest one, which at worst asks once more for the Stop check's agent. A spawn in flight when a session upgrades has no route, so its result finishes nothing, which costs at most one redo. An older `prompts.json` entry keeps `calls`, `routed` and `top_spawn` keys that nothing reads.

Writes are atomic, and state files are compact JSON. Each read-modify-write takes a lock file, because agents started in one message run their hooks at the same moment. A busy lock is retried from 1 ms, doubling to 10 ms between tries, for at most one second (`store.LOCK_WAIT_SECONDS`). If it is still held, the write is skipped and counted, and the hook goes on with what it read: a write without the lock could drop another hook's update, and a hook must never hang a session. A lock file older than ten seconds was left by a dead process, so it is removed and counted. The lock file names the session that took it, so a wait on another session's lock can be told apart. `locks.json` is written without a lock, after the state lock is let go, so two hooks at once can lose a count but never state. A spawn takes three locks on the mod path and four on the fallback ([Performance](PERFORMANCE.md#state-locks-a-spawn-takes)).

The per-session files are shared by every session, so one session can wait on another's lock. One file per session would end that wait. It stays unbuilt until `locks.json` shows such waits matter, and would have to read through to the shared files without migrating them, so an older release still works.

Each JSON state file carries a top-level `schema`, which reads strip. `store.MIGRATIONS` brings an older version up one step at a time; a file with no version is version 0. A file from a newer release is read but never written or removed, `/model-router:models` names it, and `models --reset` leaves it and says so. A file that fails to parse, isn't a JSON object, or has a `schema` that isn't a whole number of 0 or more is moved aside as `<name>.corrupt-<UTC timestamp>` before the next write, routing carries on with that state empty, and `/model-router:models` lists it. Three copies per file are kept. A file that can't be read or moved aside is left as it is and not written. Your settings and `model-router.json` files are only read. The ledger has no version: a line that doesn't parse is skipped.

Nothing about your work leaves the machine. The only network access is the daily fetch from `code.claude.com` and `platform.claude.com`, and from `api.anthropic.com` when `ANTHROPIC_API_KEY` is set, `ANTHROPIC_BASE_URL` is unset and none of `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or `CLAUDE_CODE_USE_FOUNDRY` is on.

## Trust

The user's `~/.claude/model-router.json` is trusted in full. A repository's `.claude/model-router.json` only raises. It can turn routing down to `advise` or `off`, add models, and raise any level's `needs` and `effort`, so a cloned repository can make its subagents cost more. It can't lower anything:

- A `needs` or `effort` below the shipped value, or the user's, is dropped (`config._raises_only`).
- Its agent rules only raise: `level` is a floor, `model` applies only when it isn't weaker than what the call would run on without it (`Rule.suggested`, checked in `router.decide`), and `route: false` is ignored. So it can't lower an agent whose own file pins a stronger model.
- It can't switch routing on, set prices, or re-rank a model that ships or the user defined. A name it adds that sits inside one of those names, or contains one, is dropped, and its names match a model ID last (`Registry.family`), so `claude` can't claim the shipped IDs.
- Models it adds (`Model.project`) are never the router's pick (`Registry.select`), the stand-in `Registry.clamp` gives a named model that can't run, or the next model up for a redo `maxEffortLevel` holds, and are ignored while the user has a cap. One runs only when a call or the user's own rule names it, and a run on one never counts as the strong agent the Stop check wants (`calls.strongest`).

In every file, a non-finite `needs` (`NaN`, `Infinity`, which `json` accepts) or a key of the wrong type is dropped. `config.build` lists what it dropped (`Registry.problems`) and what it held back from the project (`Registry.held`, plus rule models a spawn found weaker, kept as the spawn row's `held`); `/model-router:models` and `models --json` (`config_problems`, `held_back`) show both. The user's `max_tier` still caps every model (`Registry.available`, `Registry.clamp`), while the effort a project set stays. Override files are only read. [Security](SECURITY.md#who-can-influence-routing) maps every source of influence to its limits.

## Machine-readable output

`models --json` and `usage --json` (and the two skills with `--json`) print one JSON object, built from the same data as the tables, so the two can't disagree. `status --json` prints the upstream changes the daily check found and carries no schema key.

The top-level `schema` key is `<shape>/<version>`. A new key keeps the version, so readers ignore keys they don't know. Removing or renaming a key, or changing what one means, bumps it. The constants are `USAGE_SCHEMA` and `MODELS_SCHEMA` in `report.py`.

`model-router.usage/1`:

| Key | Contents |
| :- | :- |
| `days` | The window read from the ledger |
| `since` | Only when the ledger starts inside the window: its oldest row's `ts`. A new ledger does, and so does one cut to its newer half past 4 MB, so the window holds less than `days`. The table's heading names the date |
| `agents` | One entry per agent type, most calls first: `agent` (`?` when the call named none), `calls`, `routed` (calls the router rewrote), `levels` (level to count), `ran_on` (reported model to count) |
| `models` | One entry per reported model: `model`, `runs`, `tokens` (the sum of each run's `totalTokens`, which counts only the run's final request: https://code.claude.com/docs/en/hooks.md) and `run_tokens` (every subagent request on that model, from the mod's per-turn usage, background agents' included, so it can cover more runs than `runs`; `null` where the mod reported none). A background agent whose model wasn't reported counts under `background (not reported)`, here and in `ran_on` |
| `model_errors` | Agent calls that failed with a model error |
| `spend` | `total_usd`, `rows` (most spend first: `loop` as `main` or `subagent`, `model`, `turns`, `output_tokens`, `usd` at list price) and `unpriced` (models with no price, counted as 0) |

`model-router.models/1`:

| Key | Contents |
| :- | :- |
| `cap`, `strongest_in_use`, `priority` | The cap (`null` for none), the strongest usable model the shipped registry or your own file ranks (a repository's model only when nothing else is usable; `null` if none) and the priority |
| `models` | One entry per registry model, weakest first: `model`, `last_ran_as` (`null` if never reported), `status` and `detail` (the table's text). `status` is `working`, `not_used`, `above_cap`, `project_model_under_cap`, `not_permitted` or `routed_around` |
| `levels` | Levels the feedback loop has data for: `level`, `routed_calls`, `votes_too_low`, `raised` |
| `doc_aliases`, `not_in_registry` | Model aliases in the current docs, and the families among them the registry lacks |
| `set_aside`, `newer_files` | Paths of state files set aside as unreadable, and of state files a newer release wrote, which are only read (see State) |
| `newer_config` | Paths of `model-router.json` overrides written for a newer release, of which only the settings this release understands apply (see Compatibility) |
| `config_problems`, `held_back` | Only when non-empty. Keys an override file had wrong, as sentences; and what a project's file asked for that wasn't applied, as `kind`, `name` and `reason`, with rule models spawns held in the last 7 days (see Trust) |
| `workflow_model` | The value of `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`, or `null` |
| `locks` | Only once a lock was busy. One entry per state file: `file`, `waits` (waits that got the lock), `other_session` (waits on another session's lock), `skipped` (writes skipped), `broken` (stale locks removed) and `last_at` (Unix time of the last one) |

## Compatibility

Every shape a setup or a script depends on carries a version, so a release can change one without breaking what is already written:

| Shape | Version | Where |
| :- | :- | :- |
| State files | Top-level `schema`, a whole number | `store.SCHEMA` and `store.MIGRATIONS` (see State) |
| `config/models.json` and `model-router.json` overrides | Top-level `schema`, now 1. A file without it is version 1 | `config.CONFIG_SCHEMA` |
| `--json` output | `schema` as `<shape>/<version>` | `report.py` (see Machine-readable output) |
| Plugin options | The plugin's own version | `userConfig` in `.claude-plugin/plugin.json` |

A breaking change makes an existing file, option or reader behave differently: removing or renaming a key or an option, changing a key's type or meaning, or accepting fewer values. Adding an optional key, option or value isn't one. A breaking change to the override shape bumps `CONFIG_SCHEMA`, and `config.py` reads every older version into the new one, so old overrides keep working. Dropping an old version is a breaking change of the plugin: it needs a new major version and a CHANGELOG entry.

A project's file may no longer lower a level, and its agent rules only raise. A file that relied on either still loads, and the part that lowered is dropped and listed. That narrows what the shape accepts without changing it, so `CONFIG_SCHEMA` stays 1.

An override written for a newer release is read as far as this release understands it, and `/model-router:models` names the file. Refusing it would be worse: a newer file that sets `routing_mode: off` would turn routing back on. A state file from a newer release is read but never written, because writing it would drop what this release can't see.

Plugin options have no version key, since `userConfig` has Claude Code's shape (https://code.claude.com/docs/en/plugins/manifest-reference.md#user-configuration). They follow the plugin's version: within a major version an option is never removed or renamed, and its accepted values only grow.

## What is out of reach, or left alone on purpose

[Guide](GUIDE.md#what-it-cant-do) lists the user-facing limits. The design ones:

- Skills with `context: fork`. Their subagent starts without an `agent.spawn` event, so it isn't routed; its model comes from the skill's `model` field, else its agent type and the session. Claude Code's `/ship` reviews with `/code-review` (https://code.claude.com/docs/en/ship-command.md), which in a terminal runs as a forked subagent (https://code.claude.com/docs/en/code-review-local.md#how-findings-are-reported), so that review isn't routed either. No live run has checked it.
- Agents with no definition on disk that the engine didn't make (command-line agents), unless `agent_pins` is `override`.
- Answer quality. The profile estimates from the brief's wording and the agent's name, and redo escalation and the feedback loop react to what Claude redoes. Neither measures whether an answer was right.
- Another mod that also sets a spawn's model. Mods run in their own order; the ledger shows the model that ran.
- `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`. The env-vars page doesn't list it or say whether it beats a mod's `agent.spawn` model (https://code.claude.com/docs/en/changelog, 2.1.296); in a live run on 2.1.331 it did, so while it is set the router sets only a workflow agent's effort ([Guide](GUIDE.md#who-decides-the-model)).
- A HIPAA organization. An effort change there re-reads the context uncached (https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level), and the router has no reliable sign of that configuration, so `main_effort` still raises. Leave it off, its default, there.
- A Claude apps gateway through the Claude Desktop app. `$.session.authorize()` is null in Desktop whatever the session signed in to, and Desktop reads the gateway URL from its own managed configuration (https://code.claude.com/docs/en/claude-apps-gateway.md#deliver-policy-to-claude-desktop-sessions), which a hook can't read, so `main_effort` still raises. Leave it off there.
- The fallback's rewrite in auto mode. Auto mode reviewed the input Claude wrote, so Claude Code can deny the changed `Agent` call ("a hook changed this call's input after auto mode reviewed it"). A command hook can change Bash, PowerShell, Monitor, Edit, Write and NotebookEdit input without that denial, and Agent isn't among them (https://code.claude.com/docs/en/hooks.md#pretooluse-updatedinput-in-auto-mode). A repeat of the call runs untouched ([Rewrites that don't take](#rewrites-that-dont-take)). The mod changes no input, so the denial doesn't reach it.
- A state write while another hook holds that file's lock for over a second: it is skipped and counted, and that one update is lost ([State](#state)).
- A Stop check that can't run. When `Stop` or `SubagentStop` fails or times out, the turn ends without the checks, and `onFailure` "has no effect" on those events, since "Claude can't repair a hook that won't run" (https://code.claude.com/docs/en/hooks.md#block-the-action-when-a-hook-fails). The checks fail open, like every hook here.

## Verified live

Records of what ran. The ladder has changed since some of them (see [The ladder](#the-ladder)): the lookup agents then ran at `low` and now run at `medium`, and Sonnet at `high` or Opus at `xhigh` describes that day's ladder.

Claude Code 2.1.330, plugin from disk, headless and interactive auto mode:

- A Sonnet session's lookup and security review in one message ran on Haiku at `low` and Opus at `xhigh`; the main turn was raised, and every turn's spend logged.
- A one-stage workflow from a Sonnet session ran on Haiku at `low`.
- `/hard-inline please check src too` (headless Haiku, project skill) raised the turn to `xhigh`: turn row `hardest`, `xhigh`, `command: hard-inline`, every main-loop request at `xhigh`. The engine's order: `prompt.submit` with `command`, `skill.prompt` with the body, then `turn.start` with the `<command-message>` line; a plugin's skill is `plugin:skill` in both. $0.09.
- A complex request raised the main loop from `medium` to `high` on each request; a Haiku lookup ran at `low` on each of its own.
- Under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` with Opus, a lookup stayed on Opus at `low`, and the note said why.
- An agent that pins its model kept it and its frontmatter effort.
- Interactive auto mode: no spawn was denied, and each subagent's first request carried its routed effort.
- Three background copies of one lookup ran on Haiku at `low`; the call re-issued after they finished ran on Sonnet at `high`.
- Interactive auto mode on Haiku: a background `general-purpose` agent reported through `SubagentHandback`, which auto mode gives local subagents other than forks (https://code.claude.com/docs/en/tools-reference.md), with `last_assistant_message: null` in `SubagentStop` (https://code.claude.com/docs/en/hooks.md#subagentstop-input). The router counted it finished, and the re-issue ran standard on Sonnet at `high`. $0.08.
- Teammates, in tmux with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (https://code.claude.com/docs/en/agent-teams.md), `--teammate-mode in-process`, a Haiku lead, and no subagent model variables: two untyped teammates ran on Haiku at `low` (a listing) and Opus at `high` (a race-condition review) on every request, and two on the pinned scout and architect kept their models. $0.61.
- 2026-11-22, headless Haiku in a scratch project (`--setting-sources project`, ledger rows picked by session id), $0.07:
  - Before the slash-skill change, `/hard-inline` ran at the default `medium`: its turn text was the `<command-message>` line, which `is_user_prompt` rejects, and the body wasn't profiled.
  - A `context: fork` skill ran its subagent on Haiku with no spawn row, since no `agent.spawn` fires; its usage was logged. The skill's `model` and `effort` fields set it (https://code.claude.com/docs/en/skills.md#run-skills-in-a-subagent).
  - Plan mode: a file-listing subagent was routed to Haiku at `low`, only the engine made permission decisions, and the main loop ran on Sonnet, plan mode's upgrade of a Haiku session (https://code.claude.com/docs/en/model-config.md#opusplan-model-setting).
  - `"disableAllHooks": true`: the hooks module didn't load ("only managed plugins and built-in plugins run"), no ledger row was written, and all ran on the session's model (https://code.claude.com/docs/en/settings-reference.md#disableallhooks).

Rerun, 2026-11-22, 2.1.330, macOS, plugin from disk with its own plugins root, a clean environment, learned stats reset before the interactive part, every ledger row read back: $0.93 on a claude.ai subscription (`apiKeySource: "none"`, https://code.claude.com/docs/en/agent-sdk/typescript.md, `ApiKeySource`).

- Headless, `acceptEdits` and `bypassPermissions`: a lookup routed to Haiku at `low` (`source: router`) on every request, with no `permission_denials`.
- Headless, three background copies of one lookup ran on Haiku at `low`. First requests: the first copy wrote 3,359 cache tokens and read 8,405; the second and third each read 11,764 and wrote none. The re-issue escalated to Sonnet at `high` and wrote 11,744, reading nothing, since each model keeps its own cache (https://code.claude.com/docs/en/prompt-caching.md).
- Headless, a lookup and a security review in one message: Haiku at `low`, Opus at `high`. That prompt profiled `standard`, so the turn stayed at `medium`; a two-signal question alone raised the turn to `high` (`complex`).
- Headless, under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` with Opus, the lookup stayed on Opus at `low` and the note named the pin. The pinned `scout` kept Haiku at `low` (`source: pin`), and a one-stage workflow ran on Haiku at `low`. The docs predict otherwise if the force variable was still set (https://code.claude.com/docs/en/sub-agents.md#run-every-subagent-on-one-model), and the record doesn't say, so those two are unconfirmed until rerun.
- Headless, `/hard-inline please check src too` raised its turn to `complex`, `high` (12 of 14 requests). The earlier `hardest` record used another project's `hard-inline`, whose text isn't in the repository; this body's two signals profile as `complex` at every commit since skill profiling landed (`intake.profile_skill`, once `hooks._profile_skill`). The fork skill, plan mode and `disableAllHooks` behaved as above.
- Interactive auto mode with agent teams: a background lookup and review ran on Haiku at `low` and Opus at `high`, the re-issued lookup on Sonnet at `high`, nothing was denied, and two untyped teammates ran on Haiku at `low` and Opus at `high`. Their reports raised no lead turn: all 25 lead requests stayed at `medium`.
- Not rerun: the `Agent` call fallback, which runs only where mods don't load.

On the `Agent` call path: lookups went to Haiku and security reviews to Opus in default and auto mode, another plugin's agents were routed the same way, a Sonnet cap lowered a Fable agent, a rejected rewrite ran untouched on the retry, and on 2.1.270 the rewrite applied and the agent ran on Haiku.

Ultracode, 2026-11-24, Claude Code 2.1.331, macOS, headless `claude -p --effort ultracode`, plugin from the repository in a clean environment, $2.81 for five runs:

- Before the session floor, on a Fable 5.2 session at `xhigh`, four unpinned workflow stages ran lookup on Haiku at `medium`, standard on Sonnet at `medium`, complex on Opus at `high` and frontier on Fable at `high`; the main loop stayed at `xhigh`. `/model-router:usage` counted the four calls and $0.89, the session's cost.
- The same `Agent` call sent again climbed from standard on Sonnet at `medium` to complex on Opus at `high` (`escalated_from: standard`).
- A stage whose script named `haiku` and `low` kept both (`source: caller`).
- Under `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL=haiku`, a stage the mod set to Opus ran on Haiku, so the pin wins; the router now sets only effort under it. That run's stream didn't show ultracode.
- The verify gate asked once after an edit, the verifier passed on Haiku, and the gate didn't ask again.
- Rerun with the session floor, $1.98: on a Fable 5.2 session at `xhigh` the frontier stage ran on Fable at `xhigh` and the lookup on Haiku at `medium`; on an Opus 5.5 session at `xhigh` a complex call ran on Opus at `xhigh`, and its redo climbed to hardest on Opus at `max` (`escalated_from: complex`).
