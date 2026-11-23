<!-- Page head, written by tools/docs_assets.py: edit its PAGES table, then run it. -->
<p align="center"><img src="docs/assets/banner-changelog.svg" width="880" alt="Changelog: What changed in each release?"></p>

<p align="center"><a href="README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="docs/ROADMAP.md">Roadmap</a> &nbsp;&middot;&nbsp; Next: <a href="CONTRIBUTING.md">Contributing</a></p>
<!-- End of page head. -->

# Changelog

## Unreleased

Run `/model-router:models reset` once after upgrading. A level's learned raise now means a different model: a raise on complex used to move it from Sonnet to Opus, and now moves it from Opus to Fable. The reset clears the learned raises and the learned availability.

### Changed

- `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` is named in the session-start note and in `/model-router:models` (`workflow_model` in `--json`) as a pin on every workflow agent. Routing doesn't change for it, since the docs don't say whether it or a mod's model wins.
- Every effort the router sets, for an agent or a main-loop turn, is held to `maxEffortLevel` from your settings, managed ones included, with a `modelSettings` entry replacing its source's cap for that model. The ledger reason and `/model-router:route` say when the cap held it. Claude Code applies the cap itself, so this changes what is reported, and what a redo asks for, to what runs.
- `/model-router:route` and the `explain` command ask the same procedure a real spawn does, so they also report an agent's rule, `route: false`, the force setting, a redo and the request's floor, and they write nothing. Without `--agent` the text also reads as your own request, on its own line.
- A brief is read by one scorer, `profile.read`, for a call and for a request, and an agent's name stands for a role in one table (`profile.ROLES`), which the profile, the spawn's request floor and the Stop check all read. No level moves: 258 call briefs, 82 requests and 63 agent names keep their level and reasons.
- Complex runs on Opus at `high`, where it ran on Sonnet at `high`. Migrations, schema changes, performance work and security review get the stronger model. Hardest runs Opus at `xhigh`, so a redo of a complex call is a fresh subagent on Opus at more effort. The headline benchmark figures predate this ladder.
- A brief that fixes or hunts a defect profiles at complex at least, not standard, and so does the person's request: a fix request raises the main turn's effort and counts toward the strong-agent check. Only "fix" and "debug" count as verbs ("the fix", "fixture" and "debug logging" don't); the other hunt verbs, such as "investigate" and "diagnose", match anywhere in a brief. In the person's request a hunt verb counts only beside a word for a defect, such as "bug", "failing" or "crash", so "analyze the sales numbers in q3.csv" isn't a defect hunt. A fix the brief rules out doesn't count.
- A project's `.claude/model-router.json` may raise a level's `needs` or `effort` above the shipped (or your own) value and never lower it. A lowering is dropped with no error. A `needs` that isn't a finite number is dropped from every file.
- A rule in a project's `.claude/model-router.json` only raises. Its `level` is a floor under the level the brief earns, its `model` applies only when it isn't weaker than the router's pick for that call, and its `route: false` is ignored. Your own `~/.claude/model-router.json` keeps full control. `/model-router:models` lists what a project's file asked for and wasn't applied, and a spawn that held a rule's model writes why in the ledger's `held` field.
- A level's `needs` never falls below the level under it. A raise to one level, such as a project setting lookup to 3, lifts every level above it to at least that, where before the levels above kept their own values.
- When `maxEffortLevel` holds a redo at the model and effort of the run it repeats, the redo runs on the next stronger usable model instead, and the reason in the ledger and `/model-router:route` says so. A model named on the call, by a rule or by an agent's pin keeps its model.
- `/model-router:models` lists the keys an override file had wrong and Model Router left out (`config_problems` in `--json`), such as a `needs` of `NaN`, and the project settings it held back (`held_back`). Both keys appear only when they have something to say; `models --json` and `usage --json` are otherwise unchanged.
- `CLAUDE_CODE_SUBAGENT_EFFORT` no longer stops the router from setting a teammate's effort, since Claude Code doesn't apply that variable to teammates.
- `/model-router:refresh` starts the docs-researcher's brief with today's date, and the docs-researcher treats its training data as older than that date. On Haiku 5.5 a date with a search tool grounds answers in recent results.
- The implementer finishes the planned change and stops early only when the plan is wrong or needs the user's decision.
- The verifier reports a check that failed to start, or one that only parsed or compiled the files, as `NOT RUN`, never as a pass.
- The stronger-agent check at the end of a turn names `model-router:reviewer`, with the path of a saved diff, when code changed during the request, and the architect only for a plan still open. The reviewer stops without a diff file. It starts no lower than the request's level, like the architect and the deep-solver.
- The hand-off note on a request above the session's model says a strong agent can take the plan or decision when a few reads of the code can't settle it, and that the conversation works directly otherwise. `/model-router:ship` builds a task of a few edits, a single file or sequential steps directly, and delegates building only for more than that, or for parts that run in parallel or need their own context.
- The notes Model Router adds to Claude's context state facts and give no commands, as the hooks docs advise, so Claude Code's prompt-injection defenses don't treat them as out-of-band orders. In `advise` mode the note no longer tells Claude to pass `model` on Agent calls: a model passed on a call outranks an agent's own `model`, so it undid every pin. The session-start note says Agent calls carry no `model` or `effort` unless the user named one, since either overrides the router.
- The scout, the verifier and lookup run Haiku at `medium`, not `low`.
- Complex needs Opus, so on a Sonnet or Haiku session a complex request gets the session note and one blocked Stop asking for `model-router:architect`. `strong_agent_gate` controls it and is on by default. `priority: quality` and a learned raise take complex to Fable.
- Result rows in `ledger.jsonl` carry `models_used` when the run swapped models. Claude gets a one-line note when a risk brief's run swapped models.
- A new state file, `allowlist.json`, holds each session's settings layers.
- `verify_gate` is on by default. Claude is asked once to run the verifier when code changed and nothing checked it. Set `verify_gate: false` to turn it off.
- When `availableModels` and `deniedModels` leave no registry model, the call is left as it is, and the ledger row says why. The router no longer picks a model the lists block.
- When a cap, the allowlist or model health leaves a level's pick weaker than the level needs, the pick gets one more effort step, at most `xhigh`.
- A run that swapped models mid-run (Claude Code reports it as `modelsUsed` on the Agent result) is its own result. It no longer counts as the level's result, as a strike against the model it left, or as a redo when the call is issued again.
- The allowlist reads the settings layers through the mod, so managed policy from server-managed, MDM and registry sources reaches it. The managed settings files stay as a floor: their deny entries always apply, and their allow list must permit a model too. Without the mod, or when a read fails, it reads the files. A policy list that isn't a list, or a deny list over 200 entries, leaves no model open.
- Claude Code 2.1.329 or later is required, not 2.1.292. The mod calls `$.process.hookEnv()`, which 2.1.326 added; before it the mod routed nothing and only the fallback path worked. The manifest's `network` field needs 2.1.328, and 2.1.329 fixed the Agent tool's `effort` for named teammates, which the fallback path sets.
- In `/plugin` and `/config`, Routing mode, Priority, Strongest model allowed and Agents that name their own model are now pickers of their allowed values, so they show their default and a mistyped value can't be saved.

### Fixed

- `/model-router:usage` labels its per-model token column "Final-request tokens": Claude Code's `totalTokens` counts a subagent's final request, not its whole run. A new column, "All subagent turns' tokens", and the `run_tokens` key in `usage --json` sum every subagent request on that model from the mod's usage rows, background agents' included, so they can cover more runs than `runs` counts. `tokens` keeps its value, so the schema stays `model-router.usage/1`.
- Copies of one call that fail together on the newest ID the mod sent strike that ID, never the alias. A failure worked out which ID was sent from the current health table, and after the first failure the ID was already routed around, so the rest struck the alias.
- Calls that fail together on one model count as one outage. Each strike past the second started a new, longer outage, so a burst of parallel failures could route a model around for a week.
- An `availableModels` entry naming a specific model, such as `claude-sonnet-4-5` beside `sonnet`, turns off the family's own entry, as Claude Code applies the list. The mod could send `claude-sonnet-5-5` on a lagging provider when the list allowed only Sonnet 4.5. An entry counts as specific only when it names a version (a digit, or a `claude-` or provider prefix), so `opusplan` or `default` beside `opus` leaves the `opus` entry on.
- A hook command that fails exits 1, never 2. The launcher passed the router's exit code through, and argparse exits 2 on a bad argument, which blocks a tool call, a prompt or a stop. The launcher tells a hook by its command argument, so a command such as `explain` whose text names a hook keeps its own exit code.
- Reading the allowlist or the health table no longer writes state. A read of the mod's settings layers left them for the mod's own hooks to store, and an outage the clock made too long is shortened in memory and written by the next spawn.
- An agent name is read by whole parts, so `researcher`, `relocation-migrator`, `splinter-fixer`, `information-extractor` and `allocator-tuner` are no longer capped as search agents. `docs-researcher`, `lint-runner`, `scout`, `explorer` and `verifier` still are. `docs` counts only before another part, as before, so `docs-lookup` is capped and `update-docs` is not, and word forms such as `architecture-advisor` and `cybersecurity-analyst` still read as heavy.
- A typed `/plugin:skill args` is profiled by its arguments, not by the skill's own name.
- A background agent that hands its report back through `SubagentHandback` counts as answered. Its call used to stay open, and a re-issue wasn't seen as a redo.
- A stopped agent is counted as stopped even when its transcript can't be read.
- On Bedrock's Mantle endpoint, and with prompt caching turned off, `main_effort` leaves the main loop's effort alone, since an effort change there drops the cache.
- The Windows managed settings folder no longer comes from the `ProgramFiles` environment variable, which a trusted repository's `env` block can set. It is `C:\Program Files\ClaudeCode`.
- A later settings read that fails no longer drops a session's server-managed or MDM policy. The last policy stands, and a malformed one fails closed.
- A health record holding `NaN` or `Infinity` no longer raises or leaves a model routed around for good.
- A brief that opens with "Debug logging for ...", or rules a fix out ("avoid fixing", "must not fix"), no longer profiles as a fix. A long run of newlines in a brief no longer makes the profiler's regex scans quadratic.
- A bench check can no longer be fooled by a `unittest.py` the agent leaves in the work directory: every check runs isolated, and the root-cause checks run the work directory's tests in a child that imports `unittest` first.
- A bench run that errored during execution with a zero cost counts as an unknown cost and books the whole budget. A run the API refused for a rate limit or a lost network keeps its zero, since nothing started.

### Security

- A project's `.claude/model-router.json` can no longer hold an agent below the router's pick: its rule's `model` applies only when it isn't weaker than the pick, its `level` is a floor, and its `route: false` is ignored. With the earlier limit on lowering a level, a cloned repository can only make its subagents cost more, never run weaker than the shipped ladder. A file that relied on either keeps loading and the part that lowered is dropped; the override shape's `schema` stays 1.

### Internal

- `config.load` builds one `Registry` (models with a trust flag, ordered `Level`s, aliases, prices, the main-loop effort table, agent rules, and what it dropped or held back) from the three files, and every step reads it instead of a raw dict. `Level` is a `str` that orders by difficulty, so `max` and `<` work on levels and no step counts positions in `config.LEVELS`. The snapshot no longer reads the override files a second time to decide which models are the user's. State files, the ledger's rows and the `--json` outputs keep their shape; `tests/fixtures/json-outputs` holds the bytes of `models --json` and `usage --json` on a recorded setup.
- CI pins Claude Code: `validate` runs on 2.1.329 and 2.1.330. A new `eval` job, started by hand, runs `claude plugin eval plugins/model-router` with an `ANTHROPIC_API_KEY` secret, `--threshold 0.8`, `--max-cost-usd 2` and `--no-publish`.
- New modules in `scripts/modelrouter/`: `health` owns the record of which models failed and the rules for what counts as evidence; `allowlist` owns what `availableModels` and `deniedModels` leave open; `request` owns the current request's record in `prompts.json`; `intake` decides whether a prompt is the person's request and at what level; `ledger` writes and reads every ledger row and owns the turn Spend rule; `fallback` owns the waiting rewrites, the refused-rewrite guard and the cap-hold deny. `settings.Snapshot` reads config, options, models, health and the allowlist once per hook call and passes them down.
- `spawn` decides and records in two steps, and `fallback` adjusts the decision between them. Row shapes and the files' shapes don't change.
- The effort cache rule's model families live in `config/models.json` under `main.effort_keeps_cache`, not in `hooks.py`.
- The bench builds each row in a pure `record()` and orders runs with `schedule()`, so both are tested without a stub `claude`; the stub runs as `python stub.py` on every OS. `ledger_rows` reads through the plugin's `ledger.read`.
- `bench/analyze.py` loads the rows once, draws the benchmark chart from its summary, fills the headline figures in the docs as `<!-- bench: -->` spans, and takes `--check` to fail when `docs/BENCHMARKS.md` no longer matches the results.
- The pass checks share `bench/tasks/passcheck.py` and run with `python -I`. Six new hard tasks (a statement-queries bugfix, a cents cross-module change, a retry design, a customer-merge migration, a wallet race and an admin-route security review) run task code in a probe child where it needs one. The forced Haiku, Sonnet and Opus baselines are pinned by a test, and `bench/README.md` has the quality-tier pilot command.

## 0.1.0 - 2026-11-23

First release. Reviewed against Claude Code 2.1.330 and the platform docs of 2026-11-23; the research is in `docs/research/model-control.md`. Tested live on Claude Code 2.1.330 on macOS, headless and in an interactive terminal in auto mode. Needs Claude Code 2.1.292 or later. Licensed under the Apache License 2.0, and an installed plugin carries the license text in its own folder.

### Routing every agent

- A mod, `hooks/router.ts`, routes every subagent, teammate and workflow stage at `agent.spawn`, using the parent's exact model, and sets each routed agent's effort on every request at `turn.step`. A `PreToolUse` rewrite of the `Agent` call is the fallback where mods don't load, and stands down when the mod runs.
- Built-in, project, user and plugin agents are all routed. An agent that names its own model keeps it and gets the subtask's effort when it sets none. Workflow stages and teammate roles without a definition file are routed too.
- Five levels, profiled from the brief and the agent's name: lookup on Haiku at `low`, standard on Sonnet at `medium`, complex on Sonnet at `high`, hardest on Opus at `high`, and frontier on Fable at `high`. Only research-grade wording (open-ended, no known approach, first principles and the like) reaches frontier.
- Briefs are profiled for risk, scope, ambiguity, reasoning and load: judgment verbs such as design and review, a decision to weigh, named systems, a load limit such as a per-second rate or a millisecond budget, and a negated limit that ends with its clause. A brief in another language starts at standard. A labeled corpus of 246 briefs checks the profiler.
- Redo escalation: a call Claude re-issues within the same request runs one level higher, and never at less effort than the run it replaces. A level whose routed calls keep being redone is raised for good. A clock that moves backwards can't make an old call look like a redo.
- A model named on an `Agent` call is the caller's choice: it is kept, and a re-issue of that call isn't treated as a redo.
- On Bedrock, Google Cloud's Agent Platform, Microsoft Foundry, Claude Platform on AWS and Claude Models on Google Cloud, where an alias runs an older model, routed work starts on that family's newest model and falls back to the alias if the provider refuses it. Setting `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` keeps the alias.
- Under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` the router sets effort only, and the session-start note says what the force setting costs. `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL` and `CLAUDE_CODE_SUBAGENT_EFFORT` are respected.
- Availability tracking honors `availableModels` and `deniedModels` the way Claude Code applies them (a managed list, including `managed-settings.d` drop-in files, wins over the others; `deniedModels` is managed-only, and a denied version blocks only that version), and routes around models that fail, are substituted or are rejected on agent calls.
- A model registry with strength, speed and cost ranks and list prices, extendable from `~/.claude/model-router.json` and a project's `.claude/model-router.json`, prices included. A model a project adds can't take over a shipped model's IDs. A project's `.claude/model-router.json` may raise levels and effort as well as lower them, so a cloned repository can cost more, and `max_tier` still caps the model it gets. The registry and override files carry a `schema` version, with a written compatibility policy.

### Role agents and skills

- Seven role agents. The scout and verifier run on Haiku at `low` and the docs-researcher on Haiku at `medium`, also where the router isn't routing. The implementer, architect, reviewer and deep-solver name no model or effort, so the router routes each by its brief: the architect and reviewer start at complex, and `model-router:deep-solver` at hardest. Another plugin's solver starts no lower than complex, like the other heavy roles.
- Each description says when the agent is the right tool. The read-only agents say they write nothing, and the architect says it can't write files, so a task whose result is a file goes to an agent that can write it.
- Five skills: `ship`, `route`, `models`, `usage` and `refresh`. Skills pre-approve only the commands they run. `usage` and `models` take `--json`, with versioned schemas.

### The main conversation

- When a request needs more than the session's model, Claude is told to hand the hard thinking to the architect, or to the deep-solver for a frontier request, by name only. That hand-off starts no lower than the request's level, so a short brief can't put it below the request and a redo of it still escalates. A hand-off made while Claude handles a teammate's or another session's message routes on its brief.
- Two checks before a turn ends, each with its own option. `strong_agent_gate`, on by default, asks once for a stronger agent's check when a hard request was answered by a weaker session model alone. `verify_gate`, off by default, asks once for a test run after code in the project changed and nothing checked it; test runs inside subagents count, and it asks for a reviewer only on a risky or cross-module change.
- `main_effort`, off by default, raises the main conversation's effort for one turn when a request looks complex or harder, a pasted request or a hard skill typed as a slash command included. A skill that takes arguments is judged by them. It never lowers what you set, leaves the model alone, and is skipped wherever an effort change would re-read the context uncached, behind a Claude apps gateway included. Teammates' reports and other sessions' messages never raise it.

### Cost and visibility

- Every turn's tokens are priced at list price, one-hour cache writes and Haiku 5.5's prompts over 100K tokens at their own rates, the Mythos models included, and `/model-router:usage` shows spend by loop and model. Fast-mode requests are logged at standard rates. A version the price table doesn't list is reported as unpriced, never priced as its neighbour.
- The plugin adds 683 tokens to a Sonnet session's context, measured on a headless run ([PERFORMANCE.md](docs/PERFORMANCE.md)). Each hook starts Python once and leaves the network modules unloaded.
- Session start names a session model or alias that runs older than the newest active version of its family. A daily check reads the Claude Code changelog, docs index, model aliases and model deprecations page, plus the account's Models API list when an Anthropic API key is set, with a deadline per request, an overall budget and a backoff after a failure. Models that routed calls ran on and that are deprecated or retiring are reported at session start. A GitHub workflow runs the same check for the repository.
- Options: `routing_mode`, `priority`, `max_tier`, `agent_pins`, `main_effort`, `update_check`, `verify_gate` and `strong_agent_gate`.

### State

- State files carry a version and migrate. A file that fails to parse is set aside, a unique copy each time even within one clock tick, and only the three newest copies are kept. A file from a newer release is read but never written. `models --reset` says what it cleared.

### Measured

- Benchmarked at 10 runs per class and configuration on the routing code at `780ee86`, beside baselines on the same Claude Code version: on an Opus 5.5 session the router costs 21% less per solved task than Claude Code's defaults ($0.119 against $0.152, 80 of 80 solved each), outside the noise in 7 of 8 classes and level in design. On a Sonnet session it is cheaper only in lookup, and 13% dearer over all runs ([BENCHMARKS.md](docs/BENCHMARKS.md)).
- The benchmark harness can run a router variant with its own ladder and options (`router_config`, `plugin_options`) or from another commit (`plugin_ref`), keeps a run's scratch folder and transcripts with `--keep`, and records each task's revision on every row.
- Unit tests on Linux, macOS and Windows with Python 3.9 and 3.13, tests of the mod in Node, strict plugin validation in CI, and routing evals.

### Docs

- A short README (what it is, install, a first result, the measured result, options and limits), a guide in `docs/GUIDE.md` with the full reference, diagrams, a FAQ, a glossary, a security assessment, hook latency, state-size and context measurements, and a project site in `site/`, which a workflow deploys to GitHub Pages from `main` once Pages is on for the repository. `tools/docs_assets.py` builds the diagrams, the benchmark chart, the page heads and the site's FAQ and structured data, and `--check` fails when any of them is stale.
