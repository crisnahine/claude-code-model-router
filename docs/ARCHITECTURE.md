<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Next: <a href="../GLOSSARY.md">Glossary</a></p>
<!-- End of page head. -->

# Architecture

## The idea

Model Router sets the model and reasoning effort of every agent Claude Code starts: subagents from any plugin, skill or agent, teammates, and workflow stages. The agent the caller chose, its prompt and its tools stay as they were.

The aim is the lowest cost per finished task at a high quality bar. Every subtask starts on the model Claude names for it, and moves up when Claude marks a redo of a weak result. That trigger is Claude's judgment of the result, or of a check it saw fail. Anthropic's measured policy of re-running failures at higher effort needs a failure signal from a check (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence#re-run-failures-at-higher-effort), and no check result reaches the router, so the redo doesn't claim that policy's numbers.

## Two control paths, one router

![Two control paths, one router: Claude Code engine events reach the mod, hooks/router.ts, and the settings hooks in hooks/hooks.json are the fallback and do the bookkeeping. Both run sh model-router.sh, which calls one Python runtime that decides every spawn.](assets/two-paths.svg)

The mod (`hooks/router.ts`, loaded from `hooks/hooks.json` under `modules`) is the primary path. It hooks six engine events and asks the Python runtime through three `mod-*` commands, each payload with `session_id`:

| Event | Command and payload keys | What the mod does |
| :- | :- | :- |
| `tool.call` (`Agent`) | none | Keeps the `effort` Claude put on the call, by `tool_use_id`, since `agent.spawn` has no effort field. While the runtime answers, it marks the call for the fallback before passing it on (see below). |
| `session.start` | `mod-start`: `settings` | Sends the settings layers it read, which the runtime keeps for the session's later hooks, and clears marks older than a day. |
| `agent.spawn` | `mod-spawn`: `settings`, `parent_agent_id`, `parent_model`, `session_effort`, `kind`, `tool_use_id`, `tool_input` | Fires for every subagent, teammate and workflow agent before its model resolves, and passes the decided model on as `model`. `parentModel` is the parent's exact effective model. `kind` is `subagent`, `teammate` or `workflow`. `tool_use_id` is left out for a workflow's agents, which share one. Forks, resumed runs and `Agent` calls the mod left unmarked are left alone. |
| `turn.start` | none | An agent turn that starts before the spawn hook has the agent's id is matched by its prompt, and takes the effort its waiting spawns got, only while they all got the same one. A main-loop turn is passed on untouched. |
| `turn.step` | none | Applies the agent's routed effort to each model request. A workflow agent's is applied only when it is above the request's own, since a script can set a stage's `effort` and the spawn event doesn't carry it. Remembers the main loop's effort as `session_effort`. |
| `turn.complete` | `mod-usage`: `agent_id`, `subagent_type`, and `models`, `cost` and `subscription`, or for a workflow agent `usage` | Writes a subagent's usage to the ledger, a main-loop turn nothing. For an agent `$.agent.list()` names, it sends Claude Code's own figures for it so far: its tokens per model and its estimate in dollars, fast mode, long prompts and search fees included, and `subscription` when the session's rate limits show plan windows. A workflow's agents aren't listed, so theirs is the turn's tokens alone. |

The mod runs the launcher the settings hooks use, through `$.process.run` with the plugin's hook environment and an 8-second timeout, so there is one decision procedure and one state store. A `mod-spawn` round trip takes about 58 ms warm and 187 ms cold (p50), almost all of it starting Python ([Performance](PERFORMANCE.md#mod-path)).

The settings hooks do every job the mod doesn't: the session-start note, the note for a model the request names, availability tracking from each `Agent` result, redo bookkeeping, and the check before a turn ends. `PreToolUse` on `Agent` is the fallback path: it rewrites the call's `model` and `effort` with `updatedInput`. Plugin `PreToolUse` hooks run after the last mod calls `next` (https://code.claude.com/docs/en/plugins/mods/events.md#where-settings-hooks-run-in-the-order), so the mod's `tool.call` hook can mark a call first: it writes an empty file named for the call's `tool_use_id` under `mod-calls/` in the data folder. The fallback stands down only for a call with that mark, and removes it (`mod.routes`), so a marked call is routed once, at its spawn. The mod marks calls only while the runtime answers: `mod-start` and `mod-usage` answer `{"ok": true}` and a spawn its pick, a failed round trip stops the marks, and an empty answer, all the launcher gives when Python is missing, starts none. So a call made before the first answer, while the mod is unloaded, or while its runtime calls fail or are refused, is routed by the fallback, and the mod's spawn hook passes that call on untouched, so no call is routed twice. A spawn whose `Agent` call the mod never heard is routed by the mod. These rules rest on the docs and the mod's tests; no live run has checked them yet. It covers Claude Code versions without mods, organizations that don't load user mods, and a mod unloaded mid-session.

After `/clear` the session id is new and `session.start` doesn't fire again, so the settings hooks read the settings files for that session until its first spawn sends the layers.

The mod comes first for three reasons. It reaches teammates and workflow stages, which the `Agent` call doesn't carry. It sets the model after auto mode reviewed the call, so auto mode's "input changed after review" denial doesn't apply. And it can set effort on each request, the only lever a forced subagent model leaves.

## Components

| Module | Job |
| :- | :- |
| `config` | Builds the `Registry` from `config/models.json` and the two override files (`config.load`); holds the `Level` type |
| `registry` | The `Registry` every step reads, with `available`, `select`, `clamp`, `family`, `newest_id` and `cost` |
| `ladder` | The levels: each level's model and effort (`pick`, `model_for`), and the level a model stands for (`rung`) |
| `spawn` | The procedure both paths call: `decide`, then `record` |
| `router` | The order of authority: one decision from the facts it is given alone |
| `fallback` | The `Agent` call path's rewrite, its waiting record, the refused-rewrite guard and the one deny |
| `mod` | The mod path's commands, and `routes`, which tells the fallback a call is the mod's |
| `intake` | Whether a prompt is the person's request or a message Claude Code delivered |
| `request` | The model a prompt names (`named_model`) |
| `calls` | A `Route` per spawn, and the one rule set for what comes back (`returned`) |
| `health` | Model errors, substitutions and refused rewrites in; healthy or routed around, and last ran as, out |
| `agents` | An agent's definition: the project, the user folder, this plugin, another installed plugin, or a built-in |
| `session` | The model the conversation runs on |
| `gates` | The check before a turn ends |
| `settings` | Options, environment facts, and the `Snapshot` each hook call builds once. A read writes nothing; a spawn writes what reads noticed (`Snapshot.settle`) |
| `allowlist` | The models `availableModels` and `deniedModels` leave open, and each model's `maxEffortLevel` cap |
| `ledger` | One writer per row kind, and reading rows |
| `hooks`, `cli`, `report`, `store` | Settings-hook handlers and the command map (`hooks.HANDLERS`); the command line; tables and `--json`; state files and the ledger |

All of it is standard-library Python, plus the one TypeScript file the engine runs. No module names a model: the models, and aliases such as `best`, are entries in `config/models.json`.

## The ladder

| Level | Needs | Model | Effort |
| :- | -: | :- | :- |
| lookup | 1 | Haiku | medium |
| standard | 2 | Sonnet | medium |
| complex | 3 | Opus | high |
| hardest | 3 | Opus | xhigh |
| frontier | 4 | Fable | high |

![The ladder: lookup on Haiku at medium effort, standard on Sonnet at medium, complex on Opus at high, hardest on Opus at xhigh, frontier on Fable at high. A redo raises effort first, then the model.](assets/ladder.svg)

The router never reads a brief. Claude, which has read the work, names the model on each call, and its effort when the work needs a set one; the plugin's own agents carry theirs in their definitions. A model stands for the lowest level it runs (`ladder.rung`): Haiku for lookup, Sonnet for standard, Opus for complex, Fable for frontier. A named effort can lift it to a higher level on the same model, so Opus at `xhigh` or `max` is hardest. When nobody chose the effort, the call runs at that level's.

An earlier design scored each brief with English word patterns. No source measures routing by a brief's wording, and on work that isn't code it sent a legal summary and a literature review to Haiku, so the caller names the level instead.

Each step comes from the measurements in the research notes:

- Complex is Opus at `high`. On a SWE-bench Pro subset Opus 5.5 at `high` solved 95.3% at $0.29 per solved task, against 92.8% at $0.22 for `medium` and 87.6% at $0.11 for Sonnet 5.5 at `high`. "The bill is decided by the tasks the cheaper model fails" (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence; `docs/research/quality-first-routing.md`). A migration or a performance optimization "can have clear specs and still be difficult enough to warrant Opus 5.5" (https://claude.dev/blog/which-claude-model-should-you-build-with/, a blog post). An earlier ladder (standard at `high`, complex on Opus, hardest at `xhigh`) cost 1.2 to 2.2 times as much per solved task as Claude Code's defaults on 7 of 8 task classes on a Sonnet session, at a pass rate no lower; those rows are in `bench/results/archive/`, outside [BENCHMARKS.md](BENCHMARKS.md). That benchmark can't show what a stronger model buys on hard work: <!-- bench:opus.saturated -->5<!-- /bench --> of <!-- bench:opus.classes -->8<!-- /bench --> Opus classes are saturated, so it makes no quality claim for them.
- Standard is Sonnet 5.5 at `medium`, Claude Code's own default for it, for work with a clear spec. No rung runs Sonnet above `medium`; a caller that wants Sonnet at `high` names the effort.
- Hardest is complex's model at more effort, Opus at `xhigh`. On 463 of the subset's problems Opus 5.5 solved 95.5% at `high` for $0.30 per solved task and 96.5% at `xhigh` for $0.75, against 96.3% for Fable 5.2 at `high` for $1.39 (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence). That last point costs two and a half times as much, so only the hardest work pays it. The headline figures in BENCHMARKS.md predate this ladder: that run had complex on Sonnet.
- Frontier is Fable, for open-ended or research-grade work, and for redos of work Opus ran at `high` or above.

A level's `needs` is a strength, finite and never below the level beneath it, so a raise to one level lifts the levels above it (`config._levels` keeps a running maximum). `Registry.select` takes the cheapest usable model at or above it, so a cap or an unavailable model moves the pick without any level naming a model. The router picks this way only where nothing names a model: a rule's level and a teammate on a weak parent.

When the model that runs is weaker than the level's `needs` (a cap, the allowlist or model health left nothing at or above it), it gets one more effort step, at most `xhigh` (`ladder.effort_on`): with Opus out of reach, complex's `high` becomes `xhigh` on Sonnet. No effort the router sets on a model goes past its `effort_stops_at` in `config/models.json` (`ladder.at_most`), a level's own, the step and a redo's included, and Haiku's is `high`: its docs keep `xhigh` for work where evals show a gain over Sonnet 5.5, and at `xhigh` in a multi-turn chat it can put its whole answer in its thinking (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5#use-effort-to-control-thinking). That narrows the gap at most. The docs give no Sonnet figure at `xhigh`, and Sonnet 5.5 at `xhigh` can start its own rounds of review, which costs time and tokens (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence). A model named on the call, by a rule or by the agent's pin gets the step too when `Registry.clamp` lowers it and nobody chose the effort.

## The decision

Both paths call one procedure in two steps. `spawn.decide` gathers the facts (the agent's definition, the model it would inherit, the usable models, and for a marked redo the run it redoes) and passes them to `router.decide`. It writes nothing. `spawn.record` writes the ledger row (`via` names the path) and the spawn's route in `calls`, each under its own file's lock, so one failed write doesn't cost the other. The fallback adjusts the decision between the two steps, so the ledger row shows the guarded result. The fallback turns the decision into `updatedInput` and a note, the mod into `{model, effort, reason}`.

`router.decide` reads no state: everything arrives as facts. It returns keep or rewrite, the model, effort, level and a reason, which the ledger keeps so every choice can be explained. The `explain` command and `/model-router:route` run `spawn.decide` on a call with the agent, model and effort given, so they report what a real call would get, rules and pins included, and write nothing. They take no `description`, so they never show a redo.

Order of authority, strongest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`: the user pinned every subagent's model. The router sets effort only.
2. `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`, for a workflow agent with no model named on it. It wins over the model a mod sets (a live run on 2.1.331), so the router sets effort only.
3. A model named on the call: by Claude, the user through Claude, a skill or a workflow script.
4. A per-agent rule in a `model-router.json` file. The user's file sets the model in full. A project's file only raises: its `model` applies only when it isn't weaker than what the call would run on without it (the agent's pin, `CLAUDE_CODE_SUBAGENT_MODEL`, or the inherited model). Otherwise that stands, and the decision's `held` field and the ledger say so. The suggestion is also held when that model isn't known, as for an agent with no definition found, and when the project's own file added it. A project's `route: false` is ignored.
5. The agent's own `model`.
6. `CLAUDE_CODE_SUBAGENT_MODEL`, the user's default for agents without a model. The call is left alone.
7. Nothing: the call runs on its parent's model, as Claude Code decides, and is left alone. A teammate whose parent is weaker than the standard level's model starts on that model, Sonnet on the shipped ladder: Claude Code advises Sonnet for teammates (https://code.claude.com/docs/en/costs.md), and a teammate keeps its model for every task the lead gives it (https://code.claude.com/docs/en/agent-teams.md).

A rule's `level`, the user's or a project's, is a floor: a model that stands below it is raised to that level's model (`ladder.model_for`). Then `Registry.clamp` moves the result to the closest usable model at or below it, or to the weakest usable model when none is below, so it raises only when everything usable is stronger. The clamp also holds a call left on its parent's model, when that model is over the cap or not usable. That call then runs at the level of the model it gets, since the parent's model was nobody's choice for the work.

The scout and verifier pin Haiku at `medium` in their own definitions, so step 5 keeps them, also where the router doesn't route. At `low` the model is more likely to skip a search, stop early or skip a check in a long agent prompt (https://platform.claude.com/docs/en/build-with-claude/effort), which matters most for the verifier. The architect and reviewer pin Opus at `high`, the documented per-agent level (https://code.claude.com/docs/en/sub-agents.md#choose-an-effort-level), so a redo of theirs that names nothing moves to the next stronger model, Fable at `high`. The implementer names no model, so the caller names one per step. Where the router doesn't route, it runs on `CLAUDE_CODE_SUBAGENT_MODEL` if set, else on the conversation's model (https://code.claude.com/docs/en/sub-agents.md#choose-a-model). Another plugin's agent is read by its definition, never its name.

Effort is set to the level's whenever a model is named and nobody chose an effort: not the call, the agent's definition, `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` (`router._effort_decided`). Under steps 1 and 2 it is the pinned model's level's. The Agent tool tells Claude to name an effort only when asked to, so a call that names a model usually leaves it to the router. `CLAUDE_CODE_SUBAGENT_EFFORT` "doesn't apply to forks, forked skills, or teammates" (https://code.claude.com/docs/en/env-vars.md), so the router sets a teammate's effort anyway. The mod knows a teammate by the spawn's `kind`. On the fallback, a call from the main conversation with a `name` and no `isolation` launches as a teammate when agent teams are on (https://code.claude.com/docs/en/sub-agents.md#subagent-names), so the router treats it as one (`router.Facts.teammate`).

On the session's own model, an agent with no other effort runs at the session's level (https://code.claude.com/docs/en/model-config.md#effort-level-of-subagents-and-teammates), such as `xhigh` under ultracode. So for a call from the main conversation, an effort the router sets on that model is raised to the session's level when that is higher (`router._session_floor`), after the redo step and before the cap, on the two pinned branches too. The mod sends that level as `session_effort`; the fallback reads the hook input's `effort.level`. A nested call gets no floor, since it runs at its parent's effort. The ledger records the raised effort, so a redo asks for more than the run it replaces.

Every effort the router sets is then held to `maxEffortLevel` for the model that runs (`router.capped`, `allowlist.Allowlist.effort_cap`). The lowest cap of any source applies, a source's `modelSettings` entry for a model replaces that source's top-level cap, and `max` sets none (https://code.claude.com/docs/en/settings-reference.md#maxeffortlevel). `modelSettings` is keyed by model ID; several IDs of one family read as the highest of their caps. Claude Code applies the cap anyway; doing it here keeps the ledger and `/model-router:route` true to what runs. The reason then says "effort held to <cap> by maxEffortLevel".

Some details of how the rules apply:

- A call that would run on the same model anyway, with no effort to set, is kept as it is.
- An agent whose definition can't be found is left alone, since it may have a model its author chose. Workflow stages and teammate roles are the exception: the engine makes them, and they name no model unless the script or the call does.
- On the fallback, inside a subagent and for the built-in Explore and Plan agents, the inherited model isn't known for certain, so a call there that names no model is left alone, with no level.
- A call that names a model outside the registry, such as `inherit` or a gateway's own ID, is left as it is.
- A model the user names in a prompt (`request.named_model`) reaches Claude as a reminder to put it on that request's calls; "opus" is an ordinary word too, so the hook forces nothing.
- Where a provider's alias for the family resolves to an older model (`settings.provider`, `older_aliases`), the mod sends the family's newest ID instead (`Registry.newest_id`), except under an `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` pin, `modelOverrides`, allow or deny lists that rule the version out (`allowlist.Allowlist.permits_id`), an exact version named by the caller or agent, or Mantle, whose aliases aren't documented. One model error on that ID routes around the ID alone. The route keeps the ID sent (`sent`), and a failure reads its own spawn's route, so copies failing together on the ID never strike the alias. Only `agent.spawn` takes a full ID; the `Agent` tool's `model` takes an alias (https://code.claude.com/docs/en/sub-agents.md). [Guide](GUIDE.md#third-party-providers) lists the providers.

### Redo escalation

![Redo escalation: a call runs at standard, Sonnet at medium. Claude marks a redo, which runs Sonnet at high, and a redo of a run at high moves to complex, Opus at high.](assets/redo-escalation.svg)

Every spawn that isn't denied writes a route (`routes`, per session): its level, model, effort and who chose it. When a result comes back, one rule set (`calls.returned`) reads that route, whichever path wrote it. A result finishes the call when it isn't a background launch and didn't swap models. A finished run at a level is then kept as its task's latest (`done`), whoever named its model. The task is a hash of the agent and the `description` without the redo mark (`calls.task`), so no description text is stored; a call with no description has none. A call that names no model is recorded at the level of the model it runs on, and at the effort it runs at: with nothing that sets one, the session's level on the session's own model. A background agent's call returns at launch, so it waits under the agent's id (`launched`) and finishes when the agent stops with an answer: a closing message, or a report handed back through `SubagentHandback` (https://code.claude.com/docs/en/hooks.md#subagentstop). So copies of one call started together all run at one level. `post-agent` runs before Claude's next step, so a redo always finds the record.

Only a call whose `description` starts with "Redo:" (`calls.marked_redo`) is a redo, whatever its prompt says. Claude rewrites a brief it sends again, so the description is where the mark stays its own, and the session-start note asks Claude to keep the description and put that mark in front. No hook input marks a call as a retry, and a key outside the Agent tool's schema is dropped before hooks see it, so `description` is the one field that can carry the mark ([research](research/caller-levels-and-redo.md), Q3). The same call sent again unmarked is a new call: a check run again after a fix checks changed work, so it isn't a redo of a weak result. A redo that names its own `model` or `effort` runs as named, since the caller picked the lever: more effort for work that skipped steps, a stronger model for work that tried and was wrong.

A redo that names neither climbs from the run it redoes (`router._redo`). The router finds that run by its task (`calls.last_run`), within 30 minutes and in any request, since a redo often follows the user turning a result down. With no such run on record, the call redoes the run it would get without the mark (`spawn._unmarked_run`), which may be the parent's model at the session's level when nothing is named; its ledger row carries `on_record: false`. From that run, a redo below `high` effort runs the same model one effort step up. One at `high` or above runs the next stronger usable model at the effort of that model's own level, so Sonnet at `medium` redoes on Sonnet at `high`, then on Opus at `high`, then on Fable at `high`. With no stronger usable model it takes one more effort step, at most `max` or the model's `effort_stops_at`. A step that `maxEffortLevel`, `CLAUDE_CODE_EFFORT_LEVEL` or the model's `effort_stops_at` holds, or that would override the `CLAUDE_CODE_SUBAGENT_EFFORT` the user set, counts as no step, so the redo moves to the next model. The agent's own model and effort, a rule's model and `CLAUDE_CODE_SUBAGENT_MODEL` don't hold a redo back, and a model a project added is never the next one. The ledger's `escalated_from` names the level of the run it redoes. Each route records the effort the call runs at, in Claude Code's order (https://code.claude.com/docs/en/sub-agents.md#choose-an-effort-level): `CLAUDE_CODE_EFFORT_LEVEL`, the call's or the router's, the agent's, then `CLAUDE_CODE_SUBAGENT_EFFORT` (`router.runs_at`).

A redo is left alone where any call is: with routing off, under `route: false`, for an agent whose definition isn't found, and for a call that names a model outside the registry. Two limits hold for every redo. A workflow stage is never redone, marked or not (`spawn.facts`): its script decides what runs again, and a fan-out needs one model and effort. Where `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` pins the model, a redo takes one more effort step than the run it replaces, up to the model's `effort_stops_at`.

A redo never changes the level of later calls. `/model-router:usage` counts redos by the level they climbed from (the ledger's `escalated_from`), and a level that keeps coming back can be raised under `levels` in your `~/.claude/model-router.json` (see Trust).

A run whose model changed mid-run is its own result. Claude Code reports it as `modelsUsed` on a finished foreground `Agent` result (https://code.claude.com/docs/en/hooks.md); a content-classifier fallback can cause it, such as Sonnet 5.5 re-run on Sonnet 5 (https://code.claude.com/docs/en/model-config.md, "Automatic model fallback"). Sonnet 5 scored 10.3% against 70.6% on Terminal-Bench 4.0 (https://www.anthropic.com/news/claude-sonnet-5-5), so such a run says nothing about the level or model asked for. It isn't health evidence and doesn't finish the call. Its result row carries `models_used`, and Claude is told to check the result.

### Rewrites that don't take

On the fallback, Claude Code reports no event when the `Agent` tool rejects a rewritten call, including auto mode's denial of input a hook changed after review (https://code.claude.com/docs/en/hooks.md#pretooluse-decision-control), and rewriting the retry would loop. So every rewrite waits in `pending` until its result or failure. If the same call shows up first, 5 seconds or more later (`fallback.REFUSED_AFTER`), the rewrite didn't run, and the call passes through untouched. That passed-through or denied repeat carries no `escalated_from` in the ledger, since the first copy's row already names the climb. Copies issued in one message arrive together, so each is routed as usual. The guard lives in `fallback.py` alone. A refused rewrite to a model that has never run here is a strike against it, and two strikes route around it.

One case can't pass through: if the rewrite lowered the model and the call's untouched model is above the user's cap, the repeat is denied, with the model to use in the reason. This is the plugin's only denial, and the mod path never needs it.

### Permissions

Neither path approves anything. The hook returns `updatedInput` and a one-line note, never `permissionDecision: "allow"`, and the mod's spawn hook only passes a model on. The user's ask rules and auto mode's review of the spawn apply as they would without the plugin.

## The conversation's own model

A mod could send the main loop's requests to another model, and the first probe of this design did. The router doesn't, by choice: each model keeps its own prompt cache, so a switch re-reads the whole conversation uncached, and switching back does it again. It leaves the main loop's effort alone too. For a hard request, the person types `ultrathink` or uses `/effort` (https://code.claude.com/docs/en/model-config.md#adjust-effort-level).

`intake.py` decides whether a prompt `UserPromptSubmit` hands over is the person's, so a delivered message never gets a note. Not the person's: text framed as "Another Claude session sent a message:" over a `<teammate-message>` or `<cross-session-message>`; text that opens with a framing tag Claude Code adds (`<task-notification>`, `<command-...>`, `<bash-...>`, `<local-command-...>`, `<system-reminder>`); and text that is nothing but elements with hyphenated tag names, as every framing seen so far has. A request that opens with its own tag (`<button> does nothing on Safari`) or is written in XML tags (`<task>...</task>`) is the person's.

When the request names a model ("use opus"), the note says an Agent call with that `model` runs on it, within the user's `max_tier` when it is above the cap, or on the closest model this setup allows when `availableModels`, `deniedModels` or an outage rules it out. Under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` Claude can't pass a model (https://code.claude.com/docs/en/sub-agents.md#run-every-subagent-on-one-model), so there is no note, nor when no registry model is usable here, since Claude Code's allowlist then decides. Any other prompt gets no note and costs no extra tokens.

The `Stop` check ([Guide](GUIDE.md#the-check-before-a-turn-ends)) asks once per batch of edits. `verify_gate` asks when a file in the project (under `cwd` or `CLAUDE_PROJECT_DIR`) was edited and no check command ran after the last edit. Any file counts, since a memo or a table of figures is work too. A command is split at `&&`, `||`, `;`, `|` and newlines, even inside quotes, and a part counts when it names a test, lint or type-check tool or runner, a formatter run with `--check`, a Gradle build, or a script whose name has test, check or spec as a whole word (`run_tests.sh`, `_scratch_check.py`, not `checkout.py`), and doesn't start with a word that only installs, finds, reads, opens, copies or mentions one, runs inline code (`python -c`) or only lists tests (`--collect-only`) (`gates.is_check`): `cd api && pytest` counts, `pip install pytest`, `vim pytest.ini` and a bare `cargo build` don't. Known misses: a flag before the task, as in `make -C sub test`, doesn't count, and a flag value that reads as a script, as in `pnpm --filter test build`, does. A subagent's commands count too, read from the transcript `SubagentStop` names (`agent_transcript_path`). Starting a verifier or reviewer doesn't count; its commands do, when it stops. When the transcript can't be read (`--no-session-persistence` saves none), the check can't tell whether a check ran, so it doesn't ask. The ask is `additionalContext` on `Stop`, which keeps the turn going as hook feedback, not a hook error, under the same `stop_hook_active` guard and continuation cap as a block (https://code.claude.com/docs/en/hooks.md#stop-decision-control). Each ask writes a `gate` row to the ledger.

The check doesn't run when routing isn't `auto`, on a stop a hook already continued (`stop_hook_active`), or while a workflow, subagent, shell or teammate runs in the background (`gates.still_running`), since the turn is only paused.

## Discovery and adaptation

| What | How it is discovered | What happens |
| :- | :- | :- |
| The models that exist | `config/models.json`, plus the user's and a project's additions | They are the candidates |
| New versions of a model | Nothing to discover: routing uses aliases, and Claude Code moves each to the newest version. Where a provider's alias lags (`older_aliases`, from https://code.claude.com/docs/en/model-config.md), the mod sends the newest ID from `newest` | Picked up the day Claude Code ships them; on a lagging provider, when a release updates `newest` |
| The models you allow | The `max_tier` option | Stronger ones are never picked, and pins and explicit choices are lowered |
| The models your organization allows | `availableModels`, `deniedModels` and `availableModelsMatch`, read as Claude Code reads them, from the settings layers the mod sends, else the settings files; the managed files apply as a floor when the mod sends no policy ([Guide](GUIDE.md#organization-allowlists)) | Blocked ones are not picked. When they leave no registry model open, the call is left as it is and the ledger says why |
| The model a name resolves to | `resolvedModel` in the `Agent` result, and the model on each turn's usage | Shown in `/model-router:models` and `/model-router:usage` |
| A model that isn't served here | A model error from the `Agent` tool, twice | Routed around for a day, then retried; trouble after a retry waits three days, then seven. More failures in one outage don't stack, and a clock moved back can't stretch a wait. A call that ran on an exact model the caller or the agent named, such as a full ID or a `[1m]` variant, strikes nothing |
| A model that is silently replaced | The routed or named model against `resolvedModel`, twice, except under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` | Same |
| A model name the `Agent` tool rejects | A rewrite to it comes back without having run, twice | Same |
| A level routed too low | A call Claude marks as a redo | The redo runs one effort step up or on the next stronger model. `/model-router:usage` counts redos by level, and you raise a level in your own `model-router.json` |
| A new or removed model alias | The docs' model alias table, in the repository's daily CI check (`tools/upstream_check.py`) | An issue for the maintainer, and a registry fix in a plugin release |
| A model being deprecated or retired | The platform's "Model status" table, in the same check | The same. Claude Code itself warns at startup about a retiring model it runs ([model config](https://code.claude.com/docs/en/model-config.md)) |
| A Claude Code release, or a change to a docs page this plugin relies on | The changelog page, `llms.txt` for pages added or removed, and the full text of each page the plugin is built on, in the same check | The same |

A new model family isn't routed to until a release gives it a strength rating, since a guess could send it work it is too weak or too costly for.

The plugin itself makes no network call, so the check runs only in CI, and a fix reaches users as a plugin update. A page that comes back empty or cut short, or a request past 30 seconds, fails the CI job instead of being written to the baseline. [Security](SECURITY.md#network) has the details.

## Hook flow

The mod's events are in the table under [Two control paths](#two-control-paths-one-router). The settings hooks:

```text
SessionStart ─ session-start     records the session model; explains routing
UserPromptSubmit ─ user-prompt   notes a model the user may be asking for
PostModelSwitch ─ model-switch   records the session's new model
PreToolUse (Agent) ─ pre-agent      fallback: fallback.route, returns updatedInput; stands down for a marked call
PostToolUse (Agent) ─ post-agent    the model that ran; a finished result is kept for redos
SubagentStop (.) ─ agent-stopped    a background agent's result or handback is back; counts its checks
PostToolUseFailure (Agent) ─ agent-failed   counts model errors
PostToolUse (Edit|Write|NotebookEdit) ─ post-edit   notes that a project file changed
Stop ─ stop                      the check before a turn ends
```

`SubagentStop` matches `.`, which an empty `agent_type` doesn't, so Claude Code's internal agents (prompt suggestions, `/btw`) start no Python in a session run without `--agent` (https://code.claude.com/docs/en/hooks.md#subagentstop). Every hook is synchronous.

`cli.py` catches whatever a handler raises and exits 0, and every mod hook has a `.catch` that passes the event on unchanged. The launcher exits 0 when Python is missing and turns any other hook failure into exit 1, since exit 2 would block the tool call, the prompt or the stop (https://code.claude.com/docs/en/hooks.md#exit-code-2-behavior-per-event). A bug here can cost routing, never block a session.

## State

In `${CLAUDE_PLUGIN_DATA}` (without it, `~/.claude/plugins/data/model-router-local`):

| File | Contents |
| :- | :- |
| `state.json` | Model health |
| `allowlist.json` | Per session: each settings source's allowlist keys and `maxEffortLevel` caps, as the mod's hooks last sent them. A source whose read failed keeps its last set, else its file is read |
| `locks.json` | Per state file: lock waits, waits on another session's lock, skipped writes, stale locks broken, and when the last happened |
| `mod-calls/` | One empty file per `Agent` call the mod routes when the agent spawns, named by its `tool_use_id`: the mod's `tool.call` hook writes it, the fallback's `PreToolUse` removes it, and `mod-start` removes any older than a day |
| `sessions.json`, `routes.json`, `edits.json`, `pending.json`, `launched.json`, `done.json` | Per session: its model, each spawn's route, edit and check times, rewrites waiting for a result, background agents still running, and the latest finished run of each task, for redos. Each file keeps the 200 most recently written sessions, and each session's per-call records keep the 100 newest. `routes`, `launched` and `done` also drop a session a day after its last write (`calls.KEPT_FOR`), since every spawn rewrites them whole under one lock |
| `ledger.jsonl` | One JSON line per event, below. Rows older than 30 days go once the oldest one is, and over 4 MB its older half goes too |

Every ledger row has `ts`, `event` and `session`:

| `event` | Fields |
| :- | :- |
| `spawn` | `via`, `agent`, `action`, `level`, `model`, `was`, `effort`, `source`, `escalated_from`, `reason`; `kind` on the mod path; `held` when a project rule's model was held; `on_record: false` on a marked redo with no finished run on record |
| `result` | `agent`, `asked`, `resolved_model`, `status`; `models_used` only on a mid-run swap |
| `model_error` | `agent`, `model` |
| `gate` | `kind`: `verify` when the check before a turn ends asked |
| `usage` | A subagent's turn: `agent_id`, `agent` (its type), `models` (one entry per model: `model` and the four token counts). For an agent Claude Code lists, `running: true`, `cost_usd` (Claude Code's estimate, `null` when it gave none), `unknown_price` and `subscription` when set: these figures cover the agent's turns so far, so its newest row stands for all of them. A workflow agent's row, or one for an agent Claude Code stopped listing, is that turn's tokens alone, and the report adds it on top. The main loop writes none |

A spawn in flight when a session upgrades has no route, so its result finishes nothing, which costs at most one redo. `done.json` records an older release kept under a call's key or a plain description are never read, and go with their session.

Writes are atomic, and state files are compact JSON. Each read-modify-write takes a lock file, because agents started in one message run their hooks at the same moment. A busy lock is retried from 1 ms, doubling to 10 ms between tries, for at most one second (`store.LOCK_WAIT_SECONDS`). If it is still held, the write is skipped and counted, and the hook goes on with what it read: a write without the lock could drop another hook's update, and a hook must never hang a session. A lock file older than ten seconds was left by a dead process, so it is removed and counted. The lock file names the session that took it, so a wait on another session's lock can be told apart. `locks.json` is written without a lock, after the state lock is let go, so two hooks at once can lose a count but never state. A spawn takes two locks on the mod path and three on the fallback ([Performance](PERFORMANCE.md#state-locks-a-spawn-takes)).

The per-session files are shared by every session, so one session can wait on another's lock. One file per session would end that wait. It stays unbuilt until `locks.json` shows such waits matter, and would have to read through to the shared files without migrating them, so an older release still works. A `learned.json` left by a release that had the learning loop, a `mod.json` left by one that marked whole sessions for the fallback, a `prompts.json` left by one that kept each request's id, and an `upstream.json` left by one that fetched the docs stay in the data folder and are never read, as do the options an older release saved in `state.json`.

The shared files that several releases can write at once (`state.json`, `locks.json`, `store.DURABLE`) carry a top-level `schema`, which reads strip; a file with no version has today's shape. One from a newer release is read but never written or removed, `/model-router:models` names it, and `models --reset` leaves it and says so. One that fails to parse, isn't a JSON object, or has a `schema` that isn't a whole number of 0 or more is moved aside as `<name>.corrupt-<UTC timestamp>` before the next write, routing carries on with that state empty, and `/model-router:models` lists it. Three copies per file are kept. One that can't be read or moved aside is left as it is and not written. The per-session files carry no `schema`: one that fails to parse is replaced on its next write. Your settings and `model-router.json` files are only read. The ledger has no version: a line that doesn't parse is skipped. The data folder is set to mode 0700 and every file the Python runtime writes there is created 0600.

Nothing about your work leaves the machine, and the plugin makes no network call.

## Trust

The user's `~/.claude/model-router.json` is trusted in full. A repository's `.claude/model-router.json` only raises. It can add models, and raise any level's `needs` and `effort`, so a cloned repository can make its subagents cost more. It can't lower anything:

- A `needs` or `effort` below the shipped value, or the user's, is dropped (`config._raises_only`).
- Its agent rules only raise: `level` is a floor under any model the call runs on, `model` applies only when it isn't weaker than what the call would run on without it (`Rule.suggested`, checked in `router.decide`), and `route: false` is ignored. So it can't lower an agent whose own file pins a stronger model.
- It can't set the routing mode, which only `/config` does, or re-rank a model that ships or the user defined. A name it adds that sits inside one of those names, or contains one, is dropped, and its names match a model ID last (`Registry.family`), so `claude` can't claim the shipped IDs.
- Models it adds (`Model.project`) are never the router's pick (`Registry.select`), the stand-in `Registry.clamp` gives a named model that can't run, or the next stronger model a redo moves to, and are ignored while the user has a cap. One runs only when a call or the user's own rule names it.

In every file, a non-finite `needs` (`NaN`, `Infinity`, which `json` accepts) or a key of the wrong type is dropped. So is a project's `models` or `agents` key that isn't a short plain name (`config._PLAIN_NAME`), or `levels` key that isn't a level, since names reach skill output Claude reads; its note doesn't repeat the key. `config.build` lists what it dropped (`Registry.problems`) and what it held back from the project (`Registry.held`, plus rule models a spawn found weaker, kept as the spawn row's `held`); `/model-router:models` and `models --json` (`config_problems`, `held_back`) show both. The user's `max_tier` still caps every model (`Registry.available`, `Registry.clamp`), while the effort a project set stays. Override files are only read. [Security](SECURITY.md#who-can-influence-routing) maps every source of influence to its limits.

## Machine-readable output

`models --json` and `usage --json` print one JSON object, built from the same data as the tables, so the two can't disagree. The skills show the tables; scripts call the CLI with `--json`.

The top-level `schema` key is `<shape>/<version>`. A new key keeps the version, so readers ignore keys they don't know. Removing or renaming a key, or changing what one means, bumps it. The constants are `USAGE_SCHEMA` and `MODELS_SCHEMA` in `report.py`.

`model-router.usage/2` (version 1 had a `tokens` count per model from each run's final request, and spend by loop and model at the plugin's own list prices):

| Key | Contents |
| :- | :- |
| `days` | The window read from the ledger |
| `since` | Only when the ledger starts inside the window: its oldest row's `ts`. A new ledger does, and so does one cut to its last 30 days or to its newer half past 4 MB, so the window holds less than `days`. The table's heading names the date |
| `agents` | One entry per agent type, most calls first: `agent` (`?` when the call named none), `calls`, `routed` (calls the router rewrote), `levels` (level to count), `ran_on` (reported model to count) |
| `redos` | Only when there are any, in ladder order: the level each redo climbed from (`escalated_from`) to its count of redos. A marked redo with no finished run on record isn't counted, since nothing shows its level ran |
| `models` | One entry per reported model: `model`, `runs` and `run_tokens` (every subagent request on that model, from the ledger's `usage` rows, background agents' included, so it can cover more runs than `runs`; `null` where the mod reported none). A background agent whose model wasn't reported counts under `background (not reported)`, here and in `ran_on` |
| `model_errors` | Agent calls that failed with a model error |
| `spend` | Claude Code's estimate for subagents, not the bill: `total_usd`, `rows` (most spend first: `agent`, `runs` and `usd`), `unknown_price` (agent types whose estimate priced a model with no known price at another's rates), `no_estimate` (agent types with usage but no estimate, such as workflow agents) and `subscription` (a session in the window was on a subscription, where usage inside the plan isn't billed in dollars) |

`model-router.models/3` (version 2 also had `priority`, and `doc_aliases` and `not_in_registry`, the model aliases in the docs; version 1 also had `levels`, the learned raises):

| Key | Contents |
| :- | :- |
| `cap`, `strongest_in_use` | The cap (`null` for none), and the strongest usable model the shipped registry or your own file ranks (a repository's model only when nothing else is usable; `null` if none) |
| `models` | One entry per registry model, weakest first: `model`, `last_ran_as` (`null` if never reported), `status` and `detail` (the table's text). `status` is `working`, `not_used`, `above_cap`, `project_model_under_cap`, `not_permitted` or `routed_around` |
| `set_aside`, `newer_files` | Paths of state files set aside as unreadable, and of state files a newer release wrote, which are only read (see State) |
| `newer_config` | Paths of `model-router.json` overrides written for a newer release, of which only the settings this release understands apply (see Compatibility) |
| `config_problems`, `held_back` | Only when non-empty. Keys an override file had wrong, as sentences; and what a project's file asked for that wasn't applied, as `kind`, `name` and `reason`, with rule models spawns held in the last 7 days (see Trust) |
| `workflow_model` | The value of `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`, or `null` |
| `locks` | Only once a lock was busy. One entry per state file: `file`, `waits` (waits that got the lock), `other_session` (waits on another session's lock), `skipped` (writes skipped), `broken` (stale locks removed) and `last_at` (Unix time of the last one) |

## Compatibility

Every shape a setup or a script depends on carries a version, so a release can change one without breaking what is already written:

| Shape | Version | Where |
| :- | :- | :- |
| Shared state files | Top-level `schema`, a whole number; per-session files carry none | `store.SCHEMA`, `store.DURABLE` (see State) |
| `config/models.json` and `model-router.json` overrides | Top-level `schema`, now 1. A file without it is version 1 | `config.CONFIG_SCHEMA` |
| `--json` output | `schema` as `<shape>/<version>` | `report.py` (see Machine-readable output) |
| Plugin options | The plugin's own version | `userConfig` in `.claude-plugin/plugin.json` |

A breaking change makes an existing file, option or reader behave differently: removing or renaming a key or an option, changing a key's type or meaning, or accepting fewer values. Adding an optional key, option or value isn't one. A breaking change to the override shape bumps `CONFIG_SCHEMA`, and `config.py` reads every older version into the new one, so old overrides keep working. Dropping an old version is a breaking change of the plugin: it needs a new major version and a CHANGELOG entry.

A project's file may no longer lower a level, and its agent rules only raise. A file that relied on either still loads, and the part that lowered is dropped and listed. The same holds for `routing_mode`, which only the `/config` option sets: a file's is dropped and listed. That narrows what the shape accepts without changing it, so `CONFIG_SCHEMA` stays 1.

An override written for a newer release is read as far as this release understands it, and `/model-router:models` names the file. Refusing it would be worse: every setting this release does understand would be lost. A shared state file from a newer release is read but never written, because writing it would drop what this release can't see.

Plugin options have no version key, since `userConfig` has Claude Code's shape (https://code.claude.com/docs/en/plugins/manifest-reference.md#user-configuration). They follow the plugin's version: within a major version an option is never removed or renamed, and its accepted values only grow.

## What is out of reach, or left alone on purpose

[Guide](GUIDE.md#what-it-cant-do) lists the user-facing limits. The design ones:

- Skills with `context: fork`. Their subagent starts without an `agent.spawn` event, so it isn't routed; its model comes from the skill's `model` field, else its agent type and the session. Claude Code's `/ship` reviews with `/code-review` (https://code.claude.com/docs/en/ship-command.md), which in a terminal runs as a forked subagent (https://code.claude.com/docs/en/code-review-local.md#how-findings-are-reported), so that review isn't routed either. No live run has checked it.
- Agents with no definition on disk that the engine didn't make (command-line agents), unless the call or a rule names a model.
- Choosing the model from the work. Claude names it; a call that names none stays on its parent's model.
- Answer quality. Redo escalation reacts to what Claude redoes; it doesn't measure whether an answer was right.
- Another mod that also sets a spawn's model. Mods run in their own order; the ledger shows the model that ran.
- `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`. The env-vars page doesn't list it or say whether it beats a mod's `agent.spawn` model (https://code.claude.com/docs/en/changelog, 2.1.296); in a live run on 2.1.331 it did, so while it is set the router sets only a workflow agent's effort ([Guide](GUIDE.md#who-decides-the-model)).
- The fallback's rewrite in auto mode. Auto mode reviewed the input Claude wrote, so Claude Code can deny the changed `Agent` call ("a hook changed this call's input after auto mode reviewed it"). A command hook can change Bash, PowerShell, Monitor, Edit, Write and NotebookEdit input without that denial, and Agent isn't among them (https://code.claude.com/docs/en/hooks.md#pretooluse-updatedinput-in-auto-mode). A repeat of the call runs untouched ([Rewrites that don't take](#rewrites-that-dont-take)). The mod changes no input, so the denial doesn't reach it.
- A state write while another hook holds that file's lock for over a second: it is skipped and counted, and that one update is lost ([State](#state)).
- A Stop check that can't run. When `Stop` or `SubagentStop` fails or times out, the turn ends without the checks, and `onFailure` "has no effect" on those events, since "Claude can't repair a hook that won't run" (https://code.claude.com/docs/en/hooks.md#block-the-action-when-a-hook-fails). The checks fail open, like every hook here.

## Verified live

Records of what ran. Every call here that climbed when it was sent again unmarked used a redo the router no longer has: only a call marked "Redo:" climbs now, by an effort step before a model. The ladder has changed since some of them (see [The ladder](#the-ladder)): the lookup agents then ran at `low` and now run at `medium`, and Sonnet at `high` or Opus at `xhigh` describes that day's ladder. Every run here predates the removal of the request profile, the main-loop effort and the brief's scoring, so a main turn that was raised, or a turn's level, is behavior the router no longer has. None ran the per-call mark that decides which path routes an `Agent` call.

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
