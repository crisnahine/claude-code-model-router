# Changelog

## Unreleased

- An installed plugin now carries the MIT license text in its own folder, and the marketplace calls the product Model Router, a plugin for Claude Code.
- Claude no longer hands a task whose result is a file to an agent that can't write one. The architect, reviewer, scout, verifier and docs-researcher descriptions say they write nothing. For the architect, the hard-request note and the stronger-agent check ask for the plan, decision or check and say to make the change yourself. If the architect is still asked to write a file, it gives the file's content for the caller to write.
- The plugin adds 683 tokens to a Sonnet session's context where it added 728: the session-start note no longer names the strongest model in use, and the `route` skill drops a `when_to_use` line that repeated its description.
- The `architect` agent's and the `usage` skill's frontmatter is valid YAML again, so GitHub and other strict YAML readers no longer reject it: their descriptions are quoted, since an unquoted `: ` broke the block.
- Only research-grade wording (research-grade, open-ended, no known approach, first principles and the like) sends a brief to frontier on Fable. A brief that researches docs or writes a file named RESEARCH.json routes hardest at most.
- A doc called a migration guide no longer counts as a migration, and a file named RESEARCH.json no longer counts as research, so a docs lookup that mentions them routes on Sonnet at the level it needs.

## 0.1.1 - 2026-11-23

Reviewed against Claude Code 2.1.330 and the platform docs of the same day. Tested live on 2.1.330 on macOS, headless and in an interactive terminal in auto mode. Needs Claude Code 2.1.292 or later.

- The ladder starts cheaper: lookup on Haiku at `low`, standard on Sonnet at `medium`, complex on Sonnet at `high`, hardest on Opus at `high`, and frontier on Fable at `high`. A redo still climbs one level: lookup to standard, complex to hardest and hardest to frontier change the model, and standard to complex keeps Sonnet at more effort.
- `main_effort` is off by default, so the main conversation runs at the effort you set unless you turn the raise on.
- The two checks before a turn ends have their own options. `verify_gate`, which asks for a test run after code changes, is off by default. The new `strong_agent_gate`, which asks for a stronger agent's check when a hard request was answered by a weaker session model alone, is on.
- The implementer, architect, reviewer and deep-solver no longer pin a model or effort, so the router routes each by its brief: the architect and reviewer start at complex, and `model-router:deep-solver` is floored at hardest, from where a frontier brief can still take it higher. Another plugin's solver, such as its own `deep-solver`, starts no lower than complex, like the other heavy roles. The scout and verifier stay on Haiku at low effort and the docs-researcher on Haiku at medium, also where the router isn't routing. Their descriptions say when each is the right tool rather than asking for one on every change, and the stop check asks for a reviewer only on a risky or cross-module change. A strong agent the session note or the stop check asks for is named without a model and starts no lower than the request's level, so a short hand-off brief can't put it below the request and a redo of it still escalates.
- Load limits (throughput, latency, capacity, staleness or a per-second rate) count as a signal of their own in a brief that asks for a design or a choice, so a design brief against such a limit that also touches risk, such as data consistency, or several services routes to hardest, on Opus.
- The plugin adds less to every request's context: 728 tokens where it added 1,215, on a headless Haiku run, from a shorter session-start note, shorter role-agent descriptions and a shorter `route` skill description.
- On Bedrock, Google Cloud's Agent Platform, Microsoft Foundry, Claude Platform on AWS and Claude Models on Google Cloud, where an alias runs an older model, the mod starts routed work on that family's newest model and falls back to the alias if the provider refuses it. Setting `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` keeps the alias.
- A hard skill typed as a slash command raises that turn's effort, and a skill that takes its arguments is judged by them, so a small job given to a large pipeline skill stays at standard. An argument that only names a target, such as a file, leaves the level to the skill's own body.
- Briefs are profiled more accurately: other languages start at standard instead of lookup, a negated limit ends with its clause, and judgment verbs such as design and review, named systems and more risk words count. A labeled corpus of 243 briefs checks it.
- A brief that asks for a decision (decide how, choose a scheme, weigh the options, which library should we use) counts as reasoning and ambiguity, so a decision weighed against risk, scope or load routes like the same brief asking for a design; a load limit can be a per-second rate or a millisecond budget.
- Teammates' reports and messages from other sessions no longer raise the lead's effort.
- Each hook starts Python once and leaves the network modules unloaded, about 30 to 40 ms faster per warm hook.
- The daily check has a deadline per request and an overall budget, and after a failure waits an hour, doubling to a day, before trying again.
- State files carry a version and migrate. A file that fails to parse is set aside, one from a newer release is read but never written, and `models --reset` says what it cleared.
- `usage` and `models` take `--json`, with versioned schemas.
- Haiku 5.5's prompts over 100K tokens are priced at their own rate per request, and the Mythos models are priced. Fast-mode requests are logged at standard rates, which the README now says.
- The main conversation's effort is left alone behind a Claude apps gateway, where a change drops the cache.
- The Stop check counts test runs inside subagents before asking for a verifier.
- A clock that moves backwards no longer makes an old call look like a redo, makes a fan-out copy look like a refused rewrite, or stretches a model's outage, and only the three newest set-aside copies of a state file are kept.
- Skills pre-approve only the commands they run.
- The registry and override files carry a `schema` version, with a written compatibility policy.
- The benchmark can run a router variant with its own ladder and options (`router_config`, `plugin_options`) or from another commit (`plugin_ref`), keeps a run's scratch folder and transcripts with `--keep`, records each task's revision on every row, and names scratch folders without the task id, so a brief can't pick up signal words from its own path.
- New docs: a security assessment, hook latency, state-size and context measurements, diagrams in the README, and a glossary.

## 0.1.0 - 2026-11-22

First release. Reviewed against Claude Code 2.1.330 and the platform docs of the same day; the research is in `docs/research/model-control.md`. Tested live on 2.1.330, headless and in an interactive terminal in auto mode. Needs Claude Code 2.1.292 or later.

- A mod, `hooks/router.ts`, routes every subagent, teammate and workflow stage at `agent.spawn`, using the parent's exact model, and sets each routed agent's effort on every request at `turn.step`. A `PreToolUse` rewrite of the `Agent` call is the fallback where mods don't load, and stands down when the mod runs.
- Built-in, project, user and plugin agents are all routed. An agent that names its own model keeps it, and gets the subtask's effort when it sets none. Workflow stages and teammate roles without a definition file are routed too.
- A model registry with strength, speed and cost ranks and list prices, extendable from `~/.claude/model-router.json` and a project's `.claude/model-router.json`, prices included. A model name a project adds can't take over a shipped model's IDs.
- Five subtask levels from Anthropic's cost-per-solved-task numbers: lookup on Haiku at `low`, standard on Sonnet at `high` (its Claude Code default of `medium` scores about 7 points lower on coding), complex on Opus at `high`, hardest on Opus at `xhigh`, and frontier on Fable for research-grade work. Each is profiled from the brief and the agent's name.
- Redo escalation: a call Claude re-issues within the same request runs one level higher, and never at less effort than the run it replaces. A feedback loop raises a level for good when its routed calls keep being redone.
- The main conversation's effort rises for one turn when the request looks complex or harder, a pasted request included. It never lowers what you set, it leaves the model alone, and it is skipped where an effort change would re-read the context uncached, so the prompt cache is kept.
- Options: `routing_mode`, `priority`, `max_tier`, `agent_pins`, `main_effort`, `update_check` and `verify_gate`.
- Under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` the router sets effort only, and the session-start note says what the force setting costs. `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL` and `CLAUDE_CODE_SUBAGENT_EFFORT` are respected.
- Availability tracking that honors `availableModels` and `deniedModels` the way Claude Code applies them (a managed list, including `managed-settings.d` drop-in files, wins over the others; `deniedModels` is managed-only, and a denied version blocks only that version) and routes around models that fail, are substituted, or are rejected on agent calls.
- Every turn's tokens are priced at list price, one-hour cache writes at their own rate, and `/model-router:usage` shows spend by loop and model. A version the price table doesn't list is reported as unpriced, never priced as its neighbour.
- Session start names a session model or an alias that runs older than the newest active version of its family.
- Two checks before a turn ends: verification after code changes inside the project, and a stronger model's review when a hard request was answered by a weaker session model alone.
- Seven role agents and five skills: `ship`, `route`, `models`, `usage` and `refresh`.
- A daily check of the Claude Code changelog, docs index, model aliases and model deprecations page, plus the account's Models API list when an Anthropic API key is set, in the plugin and as a GitHub workflow. Models routed calls ran on that are deprecated or retiring are reported at session start.
- Unit tests on Linux, macOS and Windows with Python 3.9 and 3.13, tests of the mod in Node, strict plugin validation in CI, and routing evals.
