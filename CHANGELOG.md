# Changelog

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
- Unit tests on Linux, macOS and Windows with Python 3.9, 3.10 and 3.13, tests of the mod in Node, strict plugin validation in CI, and routing evals.
