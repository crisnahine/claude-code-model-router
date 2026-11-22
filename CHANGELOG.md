# Changelog

## 0.2.0 - 2026-11-22

Reviewed against Claude Code 2.1.330 and the platform docs of the same day. Tested live on 2.1.330 on macOS, headless and in an interactive terminal in auto mode. Needs Claude Code 2.1.292 or later.

- The implementer, architect, reviewer and deep-solver no longer pin a model or effort, so the router routes each by its brief: the architect and reviewer start at complex, and the deep-solver's name floors it at hardest, from where a frontier brief can still take it higher. The scout and verifier stay on Haiku at low effort and the docs-researcher on Haiku at medium, also where the router isn't routing. Their descriptions say when each is the right tool rather than asking for one on every change, and the stop check asks for a reviewer only on a risky or cross-module change. A strong agent the session note or the stop check asks for is named without a model, so the router routes its brief and a redo of it still escalates.
- On Bedrock, Google Cloud's Agent Platform, Microsoft Foundry, Claude Platform on AWS and Claude Models on Google Cloud, where an alias runs an older model, the mod starts routed work on that family's newest model and falls back to the alias if the provider refuses it. Setting `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` keeps the alias.
- A hard skill typed as a slash command raises that turn's effort, and a skill that takes its arguments is judged by them, so a small job given to a large pipeline skill stays at standard. An argument that only names a target, such as a file, leaves the level to the skill's own body.
- Briefs are profiled more accurately: other languages start at standard instead of lookup, a negated limit ends with its clause, and judgment verbs such as design and review, named systems and more risk words count. A labeled corpus of 219 briefs checks it.
- Teammates' reports and messages from other sessions no longer raise the lead's effort.
- Each hook starts Python once and leaves the network modules unloaded, about 30 to 40 ms faster per warm hook.
- The daily check has a deadline per request and an overall budget, and after a failure waits an hour, doubling to a day, before trying again.
- State files carry a version and migrate. A file that fails to parse is set aside, one from a newer release is read but never written, and `models --reset` says what it cleared.
- `usage` and `models` take `--json`, with versioned schemas.
- Haiku 5.5's prompts over 100K tokens are priced at their own rate per request, and the Mythos models are priced. Fast-mode requests are logged at standard rates, which the README now says.
- The main conversation's effort is left alone behind a Claude apps gateway, where a change drops the cache.
- The Stop check counts test runs inside subagents before asking for a verifier.
- A clock that moves backwards no longer makes an old call look like a redo or stretches a model's outage, and only the three newest set-aside copies of a state file are kept.
- Skills pre-approve only the commands they run.
- The registry and override files carry a `schema` version, with a written compatibility policy.
- New docs: a security assessment, hook latency and state-size measurements, a benchmark pilot with its raw data, diagrams in the README, and a glossary.

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
