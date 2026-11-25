<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="ARCHITECTURE.md">Architecture</a> &nbsp;&middot;&nbsp; Next: <a href="SECURITY.md">Security</a></p>
<!-- End of page head. -->

# Glossary

The project's own words, and the code behind each.

## The plugin

**Agent rule**: a rules file's `model`, `level` or `route: false` for one agent (`registry.Rule`). A project's rule only raises.

**Allowlist**: the registry models `availableModels` and `deniedModels` leave open, from the settings layers the mod sends (`allowlist.Allowlist`).

**Call**: one request to start an agent: a subagent, a teammate or a workflow stage. Its key hashes the agent type and the prompt (`calls.identify`).

**Cap**: the `max_tier` option. Every model the router keeps or sets is lowered to fit it (`Registry.clamp`), except one an environment variable pins and a fork's. A model ID the registry can't place in a family is left as it is.

**Decision**: what `router.decide` returns for a call, keep or rewrite, with model, effort, level, source and reason. The repeat guard can turn a rewrite into a deny.

**Done record**: a task's latest finished run, in `done.json`, which a redo climbs from (`calls.last_run`).

**Exact ID**: a full model ID or `[1m]` variant the call or agent names, kept while its family is (`mod.exact`).

**Fallback path**: `PreToolUse` on `Agent`, which rewrites `model` and `effort` through `updatedInput` for a call the mod didn't mark (`fallback.route`).

**Floor**: a rule's `level`, which raises a weaker model to that level's model (`Rule.level`, `Rule.floor`).

**Held**: what a project's file asked for that wasn't applied, with why: the decision's `held`, and `/model-router:models`.

**Intake**: telling the person's own prompt from a delivered message or a Claude Code framing (`intake.is_user_prompt`).

**Lagging alias**: a family whose alias runs an older model on this provider (`older_aliases`). The mod sends its newest ID instead (`Registry.newest_id`); where none goes out, the router's own pick moves one family up (`Registry.past_lag`).

**Ladder**: the five levels, each a `needs` strength and an effort (`ladder.py`, `config/models.json`).

**Ledger**: `ledger.jsonl`, one row per spawn, result, model error, Stop-check ask and subagent turn (`ledger.py`). It holds no brief or description.

**Level**: lookup, standard, complex, hardest or frontier (`config.Level`). A call's level is the one its model stands for (`ladder.rung`), raised by a floor.

**Mark**: the empty file `mod-calls/<tool_use_id>` that tells the fallback the mod routes this call (`mod.routes`). Not the redo mark.

**Mod path**: `hooks/router.ts`, which sets the model at `agent.spawn` and the effort at `turn.step`, asking the runtime through `mod-*` commands (`mod.py`).

**Model health**: model errors and substitutions seen here (`health.py`, `state.json`). Two of either route a model around for a day, and one refusal routes around a newest ID the mod sent.

**Model swap**: a run whose model changed mid-run (`modelsUsed`). It finishes no call and isn't health evidence.

**Order of authority**: where the model comes from, strongest first: the force pin, the workflow pin, the call, the user's rule, a project's rule, the agent, `CLAUDE_CODE_SUBAGENT_MODEL`, then the agent's tools (`router.decide`). An agent's `model: inherit` is its own choice of the parent's model.

**Outcome**: a result, failure or stop coming back for a call (`calls.Outcome`), read by one rule set on both paths (`calls.returned`).

**Override file**: a rules file: `~/.claude/model-router.json`, trusted in full, or a project's `.claude/model-router.json`, which only raises, merged over `config/models.json` (`config.build`).

**Pick**: the cheapest usable model for a level and its effort there (`ladder.pick`), as `policy` lists it.

**Pin**: a model an agent's own definition names (the decision's source `pin`), or its effort. The router keeps it within the cap; a redo climbs past it. The force and workflow pins are the environment variables that set every subagent's or workflow agent's model.

**Read-only agent**: one whose `tools` hold none of Edit, Write, NotebookEdit and Bash, or whose `disallowedTools` remove them all, and the built-in Explore and Plan (`agents.read_only`). A call that nothing names a model for runs at lookup on one, unless it is a teammate, and at standard on any other agent.

**Recommended setup**: a model family and the lowest effort the session that plans and delegates should run it at: Opus and Fable at `medium`, Sonnet at `xhigh`, in `config/models.json` under `session` (`Registry.session`). A family left out is never one.

**Redo**: a call whose `description` starts with "Redo:" (`calls.marked_redo`). It climbs from its task's done record, else from what it would run at unmarked, effort first, then the model (`router._redo`).

**Registry**: the models, levels, aliases, rules and newest IDs a hook call reads (`registry.Registry`).

**Repeat guard**: on the fallback path, a rewritten call that comes back unrun passes through unchanged, or is denied with the model to use when it would run above the cap (`fallback._guard`).

**Role agent**: one of the plugin's five agents, `model-router:scout`, `verifier`, `implementer`, `architect` and `reviewer`, each with its own model and effort (`agents/`).

**Route**: where one spawn went: model, ID sent, level, effort, source and path, in `routes.json` (`calls.Route`).

**Routing mode**: the `routing_mode` option, `auto` or `off`, set in `/config` and never by a rules file. `off` changes no call and gives no session note, Stop check or session check; the ledger and health still record.

**Session check**: the once-per-session question, at the start of an interactive session below the recommended setups, that offers one, then an effort (`session.check`, the `session_check` option). Only on the mod path.

**Session floor**: on the session's own model, an effort the router sets for a main-conversation call is at least the session's (`router._session_floor`).

**Session model**: the model the conversation runs on, which a call naming nothing inherits (`session.model`).

**Session note**: the table of which model and effort fit which work that Claude gets at session start (`hooks._rules`). The mod also adds it to the Agent tool's description, and a form of it to the Workflow tool's (`hooks._workflow_rules`).

**Session switch**: `/model-router on` or `off`, which sets routing for one session in `switches.json`, read by every snapshot of that session (`settings.switch`, `Settings.switched`).

**Set aside**: moving a `state.json` that fails to parse to `state.json.corrupt-<stamp>`, three kept (`store._set_aside`).

**Skipped write**: a state write dropped because another process held the lock past one second (`store.Busy`), counted in `locks.json`.

**Snapshot**: the registry, options, usable models, health and allowlist one hook call reads once (`settings.Snapshot`).

**Spawn**: a call starting its agent: `agent.spawn` on the mod path, `PreToolUse` on `Agent` on the fallback.

**Status line**: the mod's one line under the prompt: mode, path, cap, this session's calls, and how many the router changed or ran as redos (`report.status`, `$.ui.status`).

**Stop check**: the ask before a turn ends when a project file was edited and no check ran since (`gates.py`, `verify_gate`). Guidance, never a block.

**Strength**: a model's rank in the registry, Haiku 1 to Fable 4 (`Model.strength`). A level's `needs` is the weakest strength it runs on.

**Task**: a hash of a call's agent type and its description without the redo mark (`calls.task`), by which a redo finds its earlier run.

**Upstream check**: the daily CI job, `tools/upstream_check.py`, that compares Claude Code releases, the docs pages the plugin is built on, model aliases and retirements with `compat/baseline.json`. The plugin reads none of them.

**Usable**: the registry models inside the cap, permitted by the allowlist and not routed around, weakest first (`Registry.available`, `Snapshot.usable`).

## The benchmark

**Baseline**: a way of running Claude Code without the router, in `bench/configs.json`: `defaults` (no plugin, subagents on the session model) and `haiku-high` (every subagent forced onto Haiku at high effort).

**Broken run**: a run whose pass check broke, by exiting 2 or failing to start (`run.grade`). It is left out of the tables and counted in a note.

**Cost per solved task**: a configuration's total spend divided by its solved runs.

**Known answers**: the `right/` and `wrong/` answers beside a task's pass check, which a test makes the check accept and refuse.

**Pass check**: a task's script that grades a run: exit 0 solved, 2 broken, any other exit or a timeout not solved (`run.grade`).

**Saturated**: a class where an exact one-sided test at 5% can't tell `haiku-high` from `defaults`, with 10 or more runs each (`analyze._quality`).

**Task revision**: the `revision` in a task's `task.json`, kept on each row as `task_revision`. Rows on an earlier revision are left out of the tables.

**Verdict**: the router against one baseline in one class, from paired per-task resampling (`analyze._judge`): WIN, LOSE, "no difference shown", or "no verdict" when the runs can't support one (`analyze._refusal`).
