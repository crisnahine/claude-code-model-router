# Changelog

## 0.1.0 - 2026-11-21

First release, reviewed against Claude Code 2.1.330 and tested live on 2.1.270, 2.1.300 and 2.1.330.

- Seven model-pinned subagents: scout, verifier, docs-researcher (Haiku), implementer (Sonnet), architect, reviewer (Opus), deep-solver (Fable).
- Skills: `route`, `ship`, `refresh`, `usage`.
- Hooks: session routing context, model tier cap on the `Agent` tool, subagent ledger, daily upstream check.
- `max_tier` and `update_check` options in `/config`.
- Routing evals, unit tests on Linux, macOS and Windows, CI validation, and a daily upstream-watch workflow.
- The tier cap follows Claude Code's model precedence, including inherited session models, `/model` switches and `CLAUDE_CODE_SUBAGENT_MODEL`, and never judges another plugin's or the user's own agents.
- Automatic by default: the routing policy is injected every session and prompt, and generic built-in agent calls (`Explore`, `Plan`, `general-purpose`) are redirected to the orchestrator's agents. `routing_mode` option: `steer` (default), `strict`, `guide`, `off`.
- Routing hardening: parallel batches are redirected as a whole, a repeated identical call is allowed in `steer`, background-task notifications don't reset per-prompt state, a named agent stays allowed for the session, negated mentions don't count as naming, the user's own agent named like a built-in is left alone, `Plan` is left alone in plan mode, and a repository can opt out through `.claude/orchestrator.json`.
- Per-request difficulty estimate injected with each prompt, and two quality gates before a turn ends: escalation to a stronger agent when a hard request was answered by a weaker session model alone, and verification when code changed and nothing checked it. `verify_gate` option turns the gates off.

