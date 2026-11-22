# Release readiness

Checked 2026-11-22 against `v0.1.0` (`8ec8eec`) plus the unreleased commits after it, up to `ea8bf9a`, on Claude Code 2.1.330. Row IDs (S2.1 and so on) point into [TRACEABILITY.md](TRACEABILITY.md), which holds the evidence.

**Verdict: the next release is not ready.** Of the 13 release gates, 10 pass and 3 are partial. One defect is open, and the cost and quality results are a pilot. Nothing after `8ec8eec` has run in CI yet.

## Release gates

| Gate | Status | Evidence |
| :- | :- | :- |
| Every subagent, teammate and workflow stage routed on the mod path | PASS | Unit and Node tests cover all three kinds; subagents, a workflow stage and in-process teammates ran live on 2.1.330 (S10.1) |
| A working fallback where mods don't load | PASS | Fallback tests, and live runs of the `Agent` call path in default and auto mode (S10.2) |
| The precedence order and the cap honored in every case | PASS | `Router` tests; the cap holds even when a lowering rewrite didn't take (S10.3) |
| Redo escalation and learning that never loop and never lower quality | PASS | `RepeatGuard` and `Feedback` tests (S10.4) |
| Availability tracking that routes around a model that doesn't work here | PASS | `Registry` and `Router` tests (S10.5) |
| The daily upstream check, failing quietly offline | PASS | `Upstream` tests for a failed fetch, an empty feed, and a fetch that runs past its deadline (S10.6) |
| A spend ledger that matches list prices | PARTIAL | One-hour cache writes, unpriced models and Haiku's long-prompt rate per request are tested. Fast mode requests are logged at standard rates, because Claude Code doesn't say which requests ran fast (S10.7, S4.8) |
| Fail-open behaviour proven by tests | PASS | Garbage payloads to every handler; every mod hook falls back to `next(e)` (S10.8) |
| Automated tests on Linux, macOS and Windows, oldest and newest Python | PASS | CI on `8ec8eec`: Python 3.9 and 3.13 on all three, 180 tests each. The mod's Node tests now run on all three in `ci.yml`, not yet run in CI (S10.9) |
| CI green on the release PR and on the merge commit in `main`, before tagging | PASS for 0.1.0 | PR run 45004814197 and main run 45004998622 passed before `v0.1.0` was tagged. Must be redone for the next release (S10.10) |
| Reproducible cost and quality results | PARTIAL | [BENCHMARKS.md](BENCHMARKS.md): a 12-run pilot with raw data and commands, every row PARTIAL. The full suite waits on the maintainer (S10.11) |
| A security assessment | PASS | [SECURITY.md](SECURITY.md), each claim cited to the code it was checked against (S10.12) |
| Professional documentation and install steps | PARTIAL | The README has its SVG diagrams. The 0.1.0 changelog still names a Python version CI doesn't test (S10.13, S8.2) |

## Remaining defects and incomplete requirements

### Open defects

1. `CHANGELOG.md` 0.1.0 says the unit tests run on Python 3.9, 3.10 and 3.13. `ci.yml` runs 3.9 and 3.13, and so did the release run. Row S8.2.

### Decisions needed

- Waiting on the user: whether a project's `.claude/model-router.json` may raise levels and effort, or only lower them (the open question on project raises). 0.1.0 allows raises. Row S6.4.
- Fast mode in the spend log: Claude Code's own estimate in `/cost` includes fast mode, and a mod can read it. Logging it beside the router's list-price figure would change the ledger and the usage report. Rows S4.8, S10.7.
- The benchmark: the full suite, every configuration on every task 10 times, or a focused run on the questions that matter most first. The full suite is 16 tasks under 10 configurations, 10 runs each: 1,600 runs and about $176 at list price ([BENCHMARKS.md](BENCHMARKS.md) "The full suite"). Rows S4.1, S10.11, S14.1 to S14.3.
- The standard level's effort: Sonnet at `high` (shipped) or at `medium`, Claude Code's default for Sonnet. The benchmark is what would settle it. Row S10.11.
- Whether the Mythos models become a routable family. They are priced in the registry, so their spend is logged, but no level routes to them. Row S2.15.
- A live install check: installing from the marketplace into a scratch config folder and running one routed call. A scratch config folder has its own login, so the run needs the maintainer's login or an API key. Row S6.1.
- Requirement against design: the requirements say the router never blocks a spawn. On the fallback path, a repeated call whose untouched model would break the user's cap is denied, with the model to use (`docs/ARCHITECTURE.md`, "Rewrites that don't take"). Either the requirements make an exception for the cap or the design changes. Row S2.25.
- The mod's one decision: `turn.step` compares effort ranks so the main loop's effort only rises. The requirements say the mod holds no routing logic. Rows S5.2, S13.2.

### Blocked

- A fast-mode price per request: fast mode keeps the model ID, and the per-request usage Claude Code gives the mod has no speed field. Row S4.8.

### Not implemented (FAIL)

None.

### Incomplete or not verified enough (PARTIAL)

- The benchmark now has eight task classes and the `sonnet-medium`, `sonnet-high`, `fable`, `opusplan` and advisor baselines, none of them run yet (S14.1 to S14.3). CI actions are pinned to SHAs, and CI now type-checks the mod, but neither has run in CI yet (S6.12, S9.5).
- No live run: Desktop, VS Code, JetBrains and the Agent SDK (S3.7); acceptEdits and bypass modes (S3.8); which login each run used (S3.9); Bedrock, Vertex and Foundry (S3.10); `allowManagedHooksOnly` (S3.13); Linux and Windows installs (S6.1); undocumented binary behaviour beyond macOS (S5.6); no full live rerun since 0.1.0 (S9.6).
- Edge cases without a test or with a known gap: compaction (S12.7); a short brief, or a language outside the profile's list (S12.10); an empty brief (S12.11); two mods setting one spawn (S12.14); a clock moving backwards (S12.20); no Python on PATH at all (S12.23, S13.3); a Claude Code rename of an event or field (S12.26); the upstream workflow, which hasn't run on `main` yet (S6.16).
- Not measured, or only in the pilot: cache sharing across fan-outs (S4.5); `claude plugin eval` not rerun (S4.11, S9.4); cost per solved task and the baselines (S4.1, S4.12 to S4.15, S10.11, S11.8, S11.12, S14.1 to S14.3).
- Design and process: the cap denial (S2.25); the mod's effort check (S5.2, S13.2); agents with no definition on disk are left alone (S3.6); the user's decision on project raises (S6.4); no recheck for dead code (S5.5); the changelog's Python claim (S8.2, S10.13); security testing after each change (S8.3); the dogfood ledger not read (S8.4); no property-based or differential tests (S9.8); fast mode in the spend ledger (S10.7); set-aside state copies never cleaned up (S12.27); external citations not audited (S13.6).
