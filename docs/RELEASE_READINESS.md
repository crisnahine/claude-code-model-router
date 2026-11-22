# Release readiness

Checked 2026-11-22 against `v0.1.0` (`8ec8eec`) plus the unreleased commits after it, up to `fcce17a`, on Claude Code 2.1.330. Row IDs (S2.1 and so on) point into [TRACEABILITY.md](TRACEABILITY.md), which holds the evidence.

**Verdict: the next release is not ready.** Of the 13 release gates, 11 pass and 2 are partial. No defect is open. One requirement fails: on a Sonnet session, the benchmark shows the router costing more per solved task than the best baseline in 7 of 8 task classes, at the same pass rate (S4.15). On an Opus 5.5 session, Claude Code's default for most accounts, a build with the verify gate off cost 21% less per solved task than `defaults`, but on 4 runs per cell, under the 10 a PASS needs. Nothing after `8ec8eec` has run in CI yet.

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
| Reproducible cost and quality results | PASS | [BENCHMARKS.md](BENCHMARKS.md): 782 runs on all 16 tasks, on Sonnet and Opus 5.5 sessions, raw data and commands committed, $73.87 at list price. Sonnet table: 1 PASS row (lookup) and 7 FAIL. Opus table: 8 PARTIAL at 4 runs per cell (S10.11) |
| A security assessment | PASS | [SECURITY.md](SECURITY.md), each claim cited to the code it was checked against (S10.12) |
| Professional documentation and install steps | PASS | The README has its SVG diagrams, and the 0.1.0 changelog names the Python versions CI ran (S10.13, S8.2) |

## Remaining defects and incomplete requirements

### Open defects

None.

### Decisions needed

- Waiting on the user: whether a project's `.claude/model-router.json` may raise levels and effort, or only lower them (the open question on project raises). 0.1.0 allows raises. Row S6.4.
- Fast mode in the spend log: Claude Code's own estimate in `/cost` includes fast mode, and a mod can read it. Logging it beside the router's list-price figure would change the ledger and the usage report. Rows S4.8, S10.7.
- The ladder and the verify gate, from the benchmark. On a Sonnet session the shipped router cost 1.2 to 2.2 times the best baseline in every class but lookup, and no variant closed the gap in the 7 classes it lost. The cheap-first ladder, `main_effort` off and `verify_gate` off together still cost 12 to 23% more than `defaults`, from the router's own context in every run (2,300 more cache-write and 11,000 more cache-read tokens per run). On an Opus 5.5 session, `plugin-m6-08-b-nogate` cost $0.123 per solved task against $0.156 for `defaults` (95% interval of the difference -$0.048 to -$0.018), cheaper in 6 of 8 classes. With the gate on it was level and missed 2 of 4 runs of one design task. The gate adds 1.15 main-loop turns per run, and the main loop is 67 to 69% of the router's spend on Opus. Proposed: `verify_gate` off by default and a smaller injected context, measured on that commit. Row S4.15.
- The standard level's effort: Sonnet at `high` (shipped) or at `medium`, Claude Code's default for Sonnet. In the benchmark `sonnet-medium` solved implementation and known-cause bugfix tasks as often as `sonnet-high` (10 of 10 against 11 of 11), for less: $0.071 against $0.081 per solved implementation task (95% interval of the difference -$0.017 to -$0.002) and $0.057 against $0.063 per solved bugfix (-$0.012 to $0.001, not significant). Row S10.11.
- Whether to run the baselines the benchmark left out: `haiku`, `opus`, `fable`, `opusplan` and the advisor. Row S14.1.
- Whether the Mythos models become a routable family. They are priced in the registry, so their spend is logged, but no level routes to them. Row S2.15.
- A live install check: installing from the marketplace into a scratch config folder and running one routed call. A scratch config folder has its own login, so the run needs the maintainer's login or an API key. Row S6.1.
- Requirement against design: the requirements say the router never blocks a spawn. On the fallback path, a repeated call whose untouched model would break the user's cap is denied, with the model to use (`docs/ARCHITECTURE.md`, "Rewrites that don't take"). Either the requirements make an exception for the cap or the design changes. Row S2.25.
- The mod's one decision: `turn.step` compares effort ranks so the main loop's effort only rises. The requirements say the mod holds no routing logic. Rows S5.2, S13.2.

### Blocked

- A fast-mode price per request: fast mode keeps the model ID, and the per-request usage Claude Code gives the mod has no speed field. Row S4.8.

### Not implemented (FAIL)

- Beating every baseline on cost per solved task: the router wins lookup and is behind in the other seven classes at the same pass rate. Row S4.15.

### Incomplete or not verified enough (PARTIAL)

- The `haiku`, `opus`, `fable`, `opusplan` and advisor baselines haven't been run (S14.1). CI actions are pinned to SHAs, and CI now type-checks the mod, but neither has run in CI yet (S6.12, S9.5).
- No live run: Desktop, VS Code, JetBrains and the Agent SDK (S3.7); an API-key login: the subscription login is recorded, and the API-key half needs a key (S3.9); Bedrock, Vertex and Foundry (S3.10); `allowManagedHooksOnly` (S3.13); Linux and Windows installs (S6.1); undocumented binary behaviour beyond macOS (S5.6).
- Edge cases without a test or with a known gap: a short brief, or a language outside the profile's list (S12.10); on the mod path, a spawn another plugin's mod moves to another family counts as a substitution (S12.14); the upstream workflow, which hasn't run on `main` yet (S6.16).
- Not measured: accuracy per level, escalation rate, latency and cache hit rate in the benchmark report (S4.12); fan-out, workflow, team and redo shapes, cache temperature and `priority` (S4.13).
- Design and process: the cap denial (S2.25); the mod's effort check (S5.2, S13.2); agents with no definition on disk are left alone (S3.6); the user's decision on project raises (S6.4); fast mode in the spend ledger (S10.7).
