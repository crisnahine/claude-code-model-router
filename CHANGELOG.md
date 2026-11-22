# Changelog

## 0.2.0 - 2026-11-22

Reviewed against Claude Code 2.1.330 and the platform docs of the same day; the research is in `docs/research/model-control.md`. Tested live on 2.1.330, headless and in an interactive terminal in auto mode.

- A mod, `hooks/router.ts`, is now the main control path. `agent.spawn` routes every subagent, teammate and workflow stage, using the parent's exact model. `turn.step` sets each routed agent's effort on every request. The `PreToolUse` rewrite stays as the fallback and stands down when the mod runs.
- The main conversation's effort rises for one turn when the request looks complex or harder. It never lowers what you set, and it leaves the model alone, so the prompt cache is kept. New option: `main_effort`.
- A new ladder from Anthropic's cost-per-solved-task numbers: standard work on Sonnet at `high` (its Claude Code default of `medium` scores about 7 points lower on coding), complex on Opus at `high`, hardest on Opus at `xhigh`, and a new `frontier` level on Fable for research-grade work.
- Redo escalation: a call Claude re-issues after its result came back runs one level higher, and counts as a vote that its first level was too low.
- Under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` the router sets effort only, and the session-start note says what the force setting costs. `CLAUDE_CODE_SUBAGENT_EFFORT` is respected.
- Agents that pin a model and set no effort now get the subtask's effort. The `implementer` role agent runs Sonnet at `high`. `deep-solver` is for research-grade work, and `/model-router:ship` reaches it only after the architect's fix fails.
- Workflow stages and teammate roles without a definition file are routed.
- Every turn's tokens are priced at list price from a new `prices` table, and `/model-router:usage` shows spend by loop and model.
- The daily upstream check reads the platform's model deprecations page, and with an Anthropic API key and no gateway, the account's own list from the Models API. A model routed calls ran on that is deprecated or retiring is reported at session start, with its retirement date and replacement. The baseline now records model status.
- Needs Claude Code 2.1.292 or later.

## 0.1.0 - 2026-11-22

First release. Reviewed against Claude Code 2.1.330 and tested live on 2.1.270 and 2.1.330.

- Model and effort routing for every `Agent` call through a `PreToolUse` hook, covering built-in, project, user and plugin agents.
- A model registry with strength, speed and cost ranks, extendable from `~/.claude/model-router.json` and a project's `.claude/model-router.json`.
- Four subtask levels (lookup, standard, complex, hardest), profiled from each brief and the agent's name.
- Options: `routing_mode`, `priority`, `max_tier`, `agent_pins`, `update_check` and `verify_gate`.
- Availability tracking that honors `availableModels` and `deniedModels` and routes around models that fail, are substituted, or are rejected on agent calls.
- A feedback loop that raises a level when its routed calls keep being redone on a stronger model.
- Two checks before a turn ends: verification after code changes, and a stronger model's review when a hard request was answered by a weaker session model alone.
- Seven role agents and five skills: `ship`, `route`, `models`, `usage` and `refresh`.
- A daily check of the Claude Code changelog, docs index and model aliases, in the plugin and as a GitHub workflow.
- Unit tests on Linux, macOS and Windows, strict plugin validation in CI, and routing evals.
