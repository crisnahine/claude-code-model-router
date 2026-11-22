# Roadmap

What to build next, highest user value first. It starts from what 0.1.0 left undone and adds the gaps in [RELEASE_READINESS.md](RELEASE_READINESS.md). Row IDs point into [TRACEABILITY.md](TRACEABILITY.md). Move an item to "Done since 0.1.0" when its rows pass.

## Next

1. Fix the open defects. A state file that fails to parse is silently replaced and loses what was learned (in progress, m1-01). The 0.1.0 changelog names Python 3.10, which CI doesn't test. Rows S6.6, S8.2, S12.21.
2. Build the cost and quality suite and publish results. The router's whole claim is the lowest cost per solved task at a high pass rate, and nothing measures it yet. Run the baselines (Claude Code defaults, one model for every agent at default and `high` effort, `opusplan` and the advisor) on a pinned task set, publish `docs/BENCHMARKS.md` with raw data, 95% intervals and every command, and claim a win only where the data shows one. Rows S4.1, S4.12 to S4.15, S10.11, S11.8, S11.12, S14.1 to S14.3.
3. Run live beyond the macOS terminal. Linux and Windows installs; Desktop, VS Code, JetBrains and the Agent SDK; Bedrock, Vertex and Foundry; plan, acceptEdits and bypass modes; and a teammate spawned live. Record each in "Verified live". Rows S2.1, S3.2, S3.7 to S3.10, S6.1, S10.1.
4. Make the spend log match list prices. Apply Haiku's rate for prompts over 100K tokens (in progress, m1-04) and add a fast-mode rate. Rows S2.6, S4.7, S4.8, S10.7.
5. Machine-readable `/model-router:usage` and `/model-router:models`, with a versioned `schema` (in progress, m1-02). Rows S6.14, S12.28.
6. Versioned state and migrations, with corruption detection and recovery for every state file (in progress, m1-01). Rows S6.7, S12.27.
7. A security assessment in `docs/SECURITY.md` (in progress, m1-08). Rows S8.3, S10.12, S11.7.
8. Routing evals as a gate. Rerun `claude plugin eval plugins/model-router --trust-plugin`, then run the cheap evals in CI so a routing regression blocks the change. Rows S4.11, S9.4, S14.4.
9. Measure hook latency and state size, against the hook timeouts and after long use. Rows S4.5, S4.10, S11.9.
10. Confirm the CI supply-chain change in CI. Actions are pinned to commit SHAs and the mod's tests run on every OS since 0.1.0; neither has run in CI yet. Row S6.12.
11. A compatibility policy for options and config file shapes, with a version on each shape. Row S6.15.
12. SVG diagrams in the README. Row S10.13.
13. Close the untested edge cases: a brief in another language, an empty brief, compaction, a clock moving backwards, a missing Python, inline and forked skills, `disableAllHooks` and `allowManagedHooksOnly`. Add property-based tests of the profile and a differential test of the mod path against the fallback. Rows S3.4, S3.5, S3.13, S9.8, S12.7, S12.10, S12.11, S12.20, S12.23.
14. Small cleanups: glossary terms for registry, profile, priority, gate, fork and model health; a dead-code recheck; an audit of external citations. Rows S5.4, S5.5, S13.6.

## Waiting on a decision

- Whether a project's file may raise levels and effort (audit item r1-04). The user decides; 0.1.0 allows raises. Row S6.4.
- Whether the fallback's one denial, which protects the cap, stays as an exception to "never blocks a spawn". Row S2.25.

## Done since 0.1.0

- The state documents: this file, `TRACEABILITY.md` with its count script, `RELEASE_READINESS.md`, `TEST_RESULTS.md`, the `docs/README.md` index and `CONTRIBUTING.md`.
