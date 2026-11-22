# Roadmap

What to build next, highest user value first. It starts from what 0.1.0 left undone and adds the gaps in [RELEASE_READINESS.md](RELEASE_READINESS.md). Row IDs point into [TRACEABILITY.md](TRACEABILITY.md). Move an item to "Done since 0.1.0" when its rows pass.

## Next

1. Fix the open defect: the 0.1.0 changelog names Python 3.10, which CI doesn't test. Row S8.2.
2. Run the full cost and quality suite and publish it. The router's whole claim is the lowest cost per solved task at a high pass rate, and only a 12-run pilot measures it. Run every configuration 10 times on the pinned tasks once the maintainer agrees to the cost, add the missing baselines (one model at `high`, `opusplan`, the advisor), fan-outs, cache temperature and router overhead, and claim a win only where the data shows one. Rows S4.1, S4.12 to S4.15, S10.11, S11.8, S11.12, S14.1 to S14.3.
3. Run live beyond the macOS terminal. Linux and Windows installs; Desktop, VS Code, JetBrains and the Agent SDK; Bedrock, Vertex and Foundry; acceptEdits and bypass modes; `allowManagedHooksOnly`; split-pane teammates. Record each in "Verified live". Rows S3.7 to S3.10, S3.13, S6.1.
4. Decide how fast mode shows in the spend log. Claude Code doesn't say which requests ran fast, so the only full figure is its own estimate in `/cost`, which a mod can read. Rows S4.8, S10.7.
5. A security assessment in `docs/SECURITY.md` (in progress). Rows S8.3, S10.12, S11.7.
6. Measure hook latency and state size, against the hook timeouts and after long use (in progress). Rows S4.5, S4.10, S11.9.
7. Raise the main loop's effort for a hard skill typed as a slash command, which ran at the session's default effort live (in progress). Row S3.4.
8. Rerun `claude plugin eval plugins/model-router --trust-plugin` and record its cost. Rows S4.11, S9.4.
9. Confirm the CI supply-chain change in CI. Actions are pinned to commit SHAs and the mod's tests run on every OS since 0.1.0; neither has run in CI yet. Row S6.12.
10. Clean up set-aside state copies after a while, so a run of corrupt files can't grow the data folder for good. Row S12.27.
11. Close the untested edge cases: an empty brief, a short or unlisted-language brief, compaction, a clock moving backwards, a missing Python, two mods setting one spawn, a renamed event or field. Add property-based tests of the profile and a differential test of the mod path against the fallback. Rows S9.8, S12.7, S12.10, S12.11, S12.14, S12.20, S12.23, S12.26.
12. Small cleanups: glossary terms for registry, profile, priority, gate, fork and model health; a dead-code recheck; an audit of external citations. Rows S5.4, S5.5, S13.6.

## Waiting on a decision

- Whether a project's file may raise levels and effort (the open question on project raises). The user decides; 0.1.0 allows raises. Row S6.4.
- Whether the fallback's one denial, which protects the cap, stays as an exception to "never blocks a spawn". Row S2.25.
- Whether to spend about $21 on the full benchmark. Row S10.11.

## Done since 0.1.0

- The state documents: this file, `TRACEABILITY.md` with its count script, `RELEASE_READINESS.md`, `TEST_RESULTS.md`, the `docs/README.md` index and `CONTRIBUTING.md`.
- State files carry a version and migrate; one that fails to parse is set aside, and one from a newer release is read but never written. Rows S6.6, S6.7, S12.21.
- `models --reset` says what it cleared and leaves files it can't safely touch. Row S12.28.
- Each request is priced on its own, with Haiku's rate for prompts over 100K tokens. Rows S2.6, S4.7.
- `--json` for `usage` and `models`, with versioned schemas. Rows S6.14, S12.28.
- Teammates verified live on 2.1.330. Rows S2.1, S3.2, S10.1.
- The routing evals' expected routes are checked in the unit gate, on every CI cell. Row S14.4.
- A benchmark harness and a first pilot, with saved sessions so the router's Stop check can see a run's tests. Row S10.11 (partial).
- SVG diagrams in the README. Row S10.13 (partial).
- Briefs in other languages start at standard; a negated limit ends with its clause; teammates' reports no longer raise the lead's effort. Rows S12.10, S12.12, S12.13.
- A `schema` version on the registry and overrides, and a written compatibility policy. Row S6.15.
- Inline and forked skills, plan mode and `disableAllHooks` checked live; forked skills stay on the session's model. Rows S3.5, S3.8, S3.13 (partial).
- Skills pre-approve only their own commands; the daily fetch has a deadline; the Stop check counts test runs inside subagents. Rows S12.29, S10.6, S2.23.
