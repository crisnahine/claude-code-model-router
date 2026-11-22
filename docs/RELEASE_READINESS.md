# Release readiness

Checked 2026-11-23 for 0.1.1, against `v0.1.0` (`8ec8eec`) plus the unreleased commits after it, up to `e00a9a6`, on Claude Code 2.1.330. Row IDs (S2.1 and so on) point into [TRACEABILITY.md](TRACEABILITY.md), which holds the evidence.

**Verdict: 0.1.1 ships by the maintainer's decision, with one requirement failing (S4.15), 20 partial and 1 blocked; see below.** Of the 13 release gates, 11 pass; the spend ledger is partial, and CI on the release PR and on its merge into `main` waits for the release, which RELEASING.md items 5 and 6 check before the tag. No defect is open. The requirement that fails: on a Sonnet session, the benchmark shows the router costing more per solved task than the best baseline in 7 of 8 task classes, at the same pass rate (S4.15). On an Opus 5.5 session, Claude Code's default for most accounts, a build with the verify gate off cost 21% less per solved task than `defaults`, but on 4 runs per cell, under the 10 a PASS needs. The confirmation on the shipped commit (`a06e5d4`, 1 run per task) cost $0.124 per solved task against $0.156 and solved 15 of 16, a difference inside the noise. Nothing after `8ec8eec` has run in CI yet.

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
| CI green on the release PR and on the merge commit in `main`, before tagging | NOT YET for 0.1.1 | Nothing after `8ec8eec` has run in CI. It runs on the release PR and its merge (RELEASING.md items 5 and 6). For 0.1.0, PR run 45004814197 and main run 45004998622 passed before `v0.1.0` was tagged (S10.10) |
| Reproducible cost and quality results | PASS | [BENCHMARKS.md](BENCHMARKS.md): 798 runs on all 16 tasks, on Sonnet and Opus 5.5 sessions, raw data and commands committed, $75.74 at list price. Sonnet table: 1 PASS row (lookup) and 7 FAIL. Opus table: 8 PARTIAL at 4 runs per cell, plus the confirmation on the shipped commit (S10.11) |
| A security assessment | PASS | [SECURITY.md](SECURITY.md), each claim cited to the code it was checked against (S10.12) |
| Professional documentation and install steps | PASS | The README has its SVG diagrams, the changelog has a 0.1.1 section, and the 0.1.0 section names the Python versions CI ran (S10.13, S8.2) |

## Remaining defects and incomplete requirements

### Open defects

None.

### Decisions needed

- Waiting on the user: whether a project's `.claude/model-router.json` may raise levels and effort, or only lower them (the open question on project raises). 0.1.0 allows raises. Row S6.4.
- Fast mode in the spend log: Claude Code's own estimate in `/cost` includes fast mode, and a mod can read it. Logging it beside the router's list-price figure would change the ledger and the usage report. Rows S4.8, S10.7.
- Settled: the ladder and the standard level's effort. The router cost 1.2 to 2.2 times the best baseline per solved task in every class but lookup, at the same pass rate, and `sonnet-medium` solved implementation and known-cause bugfix tasks as often as `sonnet-high` for less. So the ladder now starts cheap (standard on Sonnet at `medium`, complex on Sonnet at `high`, hardest on Opus at `high`), and `main_effort` is off by default. Rows S4.15, S10.11.
- Found by the rerun, on a Sonnet session and before `f9b9a1b` cut the context the plugin adds: the cheap ladder didn't close the gap. With `main_effort` and `verify_gate` off too, the router still cost 10 to 25% more per solved task than `defaults` in the 7 classes it lost, from its own context in every run (2,300 more cache-write and 11,000 more cache-read tokens per run). Rows S4.15, S10.11.
- Found by the rerun, on an Opus 5.5 session, the `default` model for every account type but Microsoft Foundry (https://code.claude.com/docs/en/model-config.md): `plugin-m6-08-b-nogate` cost $0.123 per solved task against $0.156 for `defaults` (95% interval of the difference -$0.048 to -$0.019), cheaper in 6 of 8 classes, and with the verify gate on it was level. The gate adds 1.15 main-loop turns per run, and the main loop takes 64 to 69% of the router's spend on Opus, so the gate is now off by default. The shipped commit `a06e5d4`, with the gate off and the smaller context, cost $0.124 per solved task against $0.156 (interval -$0.056 to +$0.003) and solved 15 of 16, on 1 run per task. Rows S4.15, S10.11.
- Found by the rerun: one design task is a quality risk. Handed to the Sonnet `implementer`, `design-cache-invalidation` passed 1 of 4 times across the router builds on Opus, where `defaults` on Opus passed 4 of 4. Row S4.15.
- Found by the benchmark, for the standard level's effort: `sonnet-medium` solved implementation and known-cause bugfix tasks as often as `sonnet-high` (10 of 10 against 11 of 11), for less: $0.071 against $0.081 per solved implementation task (95% interval of the difference -$0.017 to -$0.002) and $0.057 against $0.063 per solved bugfix (-$0.012 to $0.001, not significant). Row S10.11.
- Whether to run the baselines the benchmark left out: `haiku`, `opus`, `fable`, `opusplan` and the advisor. Row S14.1.
- Whether the Mythos models become a routable family. They are priced in the registry, so their spend is logged, but no level routes to them. Row S2.15.
- A live install check: installing from the marketplace into a scratch config folder and running one routed call. A scratch config folder has its own login, so the run needs the maintainer's login or an API key. Row S6.1.
- Requirement against design: the requirements say the router never blocks a spawn. On the fallback path, a repeated call whose untouched model would break the user's cap is denied, with the model to use (`docs/ARCHITECTURE.md`, "Rewrites that don't take"). Either the requirements make an exception for the cap or the design changes. Row S2.25.
- The mod's one decision: `turn.step` compares effort ranks so the main loop's effort only rises. The requirements say the mod holds no routing logic. Rows S5.2, S13.2.

### Blocked

- A fast-mode price per request: fast mode keeps the model ID, and the per-request usage Claude Code gives the mod has no speed field. Row S4.8.

### Not implemented (FAIL)

- Beating every baseline on cost per solved task: on a Sonnet session the router wins lookup and is behind in the other seven classes at the same pass rate. The Opus 5.5 rows are under the 10 runs a PASS needs. Row S4.15.

### Incomplete or not verified enough (PARTIAL)

- The `haiku`, `opus`, `fable`, `opusplan` and advisor baselines haven't been run (S14.1). CI actions are pinned to SHAs, and CI now type-checks the mod, but neither has run in CI yet (S6.12, S9.5).
- No live run: Desktop, VS Code, JetBrains and the Agent SDK (S3.7); an API-key login: the subscription login is recorded, and the API-key half needs a key (S3.9); Bedrock, Vertex and Foundry (S3.10); `allowManagedHooksOnly` (S3.13); Linux and Windows installs (S6.1); undocumented binary behaviour beyond macOS (S5.6).
- Edge cases without a test or with a known gap: a short brief, or a language outside the profile's list (S12.10); on the mod path, a spawn another plugin's mod moves to another family counts as a substitution (S12.14); the upstream workflow, which hasn't run on `main` yet (S6.16).
- Not measured: accuracy per level, escalation rate, latency and cache hit rate in the benchmark report (S4.12); fan-out, workflow, team and redo shapes, cache temperature and `priority` (S4.13).
- Design and process: the cap denial (S2.25); the mod's effort check (S5.2, S13.2); agents with no definition on disk are left alone (S3.6); the user's decision on project raises (S6.4); fast mode in the spend ledger (S10.7).
