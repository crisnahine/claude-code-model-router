# Changelog

## 0.1.0 - 2026-11-21

First release, reviewed against Claude Code 2.1.330 and tested live on 2.1.270, 2.1.300 and 2.1.330.

- Seven model-pinned subagents: scout, verifier, docs-researcher (Haiku), implementer (Sonnet), architect, reviewer (Opus), deep-solver (Fable).
- Skills: `route`, `ship`, `refresh`, `usage`.
- Hooks: session routing context, model tier cap on the `Agent` tool, subagent ledger, daily upstream check.
- `max_tier` and `update_check` options in `/config`.
- Routing evals, unit tests on Linux, macOS and Windows, CI validation, and a daily upstream-watch workflow.
- The tier cap follows Claude Code's model precedence, including inherited session models, `/model` switches and `CLAUDE_CODE_SUBAGENT_MODEL`, and never judges another plugin's or the user's own agents.
