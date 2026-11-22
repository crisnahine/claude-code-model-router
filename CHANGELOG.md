# Changelog

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
