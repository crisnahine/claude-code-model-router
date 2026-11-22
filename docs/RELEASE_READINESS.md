# Release readiness

Checked 2026-11-22 against `v0.1.0` (`8ec8eec`) plus the unreleased commits after it, on Claude Code 2.1.330. Row IDs (S2.1 and so on) point into [TRACEABILITY.md](TRACEABILITY.md), which holds the evidence.

**Verdict: the next release is not ready.** Of the 13 release gates, 8 pass, 4 are partial and 1 fails. The failing gate is reproducible cost and quality results, which don't exist yet. 0.1.0 shipped against the same gates with these gaps open.

## Release gates

| Gate | Status | Evidence |
| :- | :- | :- |
| Every subagent, teammate and workflow stage routed on the mod path | PARTIAL | Unit and Node tests cover all three kinds; subagents and a workflow stage ran live on 2.1.330. No teammate has been spawned live (S10.1) |
| A working fallback where mods don't load | PASS | Fallback tests, and live runs of the `Agent` call path in default and auto mode (S10.2) |
| The precedence order and the cap honored in every case | PASS | `Router` tests; the cap holds even when a lowering rewrite didn't take (S10.3) |
| Redo escalation and learning that never loop and never lower quality | PASS | `RepeatGuard` and `Feedback` tests (S10.4) |
| Availability tracking that routes around a model that doesn't work here | PASS | `Registry` and `Router` tests (S10.5) |
| The daily upstream check, failing quietly offline | PASS | `Upstream` tests for a failed fetch and an empty feed (S10.6) |
| A spend ledger that matches list prices | PARTIAL | One-hour cache writes and unpriced models are tested. Haiku's long-prompt rate isn't applied (in progress, m1-04), and there is no fast-mode rate (S10.7) |
| Fail-open behaviour proven by tests | PASS | Garbage payloads to every handler; every mod hook falls back to `next(e)` (S10.8) |
| Automated tests on Linux, macOS and Windows, oldest and newest Python | PASS | CI on `8ec8eec`: Python 3.9 and 3.13 on all three, 180 tests each. The mod's Node tests now run on all three in `ci.yml`, not yet run in CI (S10.9) |
| CI green on the release PR and on the merge commit in `main`, before tagging | PASS for 0.1.0 | PR run 45004814197 and main run 45004998622 passed before `v0.1.0` was tagged. Must be redone for the next release (S10.10) |
| Reproducible cost and quality results | FAIL | No suite, no `docs/BENCHMARKS.md` (S10.11) |
| A security assessment | PARTIAL | `docs/SECURITY.md` in progress (m1-08) (S10.12) |
| Professional documentation and install steps | PARTIAL | README has no SVG diagrams, and the 0.1.0 changelog names a Python version CI doesn't test (S10.13, S8.2) |

## Remaining defects and incomplete requirements

### Open defects

1. A state file that fails to parse is silently replaced, losing learned votes, model health and call history. In progress (m1-01). Rows S6.6, S12.21.
2. `CHANGELOG.md` 0.1.0 says the unit tests run on Python 3.9, 3.10 and 3.13. `ci.yml` runs 3.9 and 3.13, and so did the release run. Row S8.2.
3. The spend log prices whole turns at Haiku's base rate, so a Haiku request over 100K prompt tokens is under-priced. In progress (m1-04). Rows S2.6, S4.7, S10.7.

### Decisions needed

- Waiting on the user: whether a project's `.claude/model-router.json` may raise levels and effort, or only lower them (audit item r1-04). 0.1.0 allows raises. Row S6.4.
- Requirement against design: the requirements say the router never blocks a spawn. On the fallback path, a repeated call whose untouched model would break the user's cap is denied, with the model to use (`docs/ARCHITECTURE.md`, "Rewrites that don't take"). Either the mission makes an exception for the cap or the design changes. Row S2.25.
- The mod's one decision: `turn.step` compares effort ranks so the main loop's effort only rises. The requirements say the mod holds no routing logic. Rows S5.2, S13.2.

### Not implemented (FAIL)

- The whole cost and quality programme: cost per solved task as the measure, the metrics, the task matrix, published results, the baselines, a pinned suite, results with confidence intervals, and the comparison. Rows S4.1, S4.12 to S4.15, S10.11, S11.8, S11.12, S14.1 to S14.3.
- Routing evals in CI. Row S14.4.
- A fast-mode price. Row S4.8.
- Hook latency and state-size measurements. Row S11.9.
- Versioned options and config file shapes with a compatibility policy. Row S6.15.

### Incomplete or not verified enough (PARTIAL)

- In progress this round: state corruption, recovery and versioned migration (S6.6, S6.7, S12.21, S12.27; m1-01); machine-readable `usage` and `models` (S6.14, S12.28; m1-02); Haiku's long-prompt rate (S2.6, S4.7, S10.7; m1-04); the security assessment (S10.12, S11.7; m1-08). CI actions are pinned to SHAs but that hasn't run in CI yet (S6.12).
- No live run: a teammate (S2.1, S3.2, S10.1); Desktop, VS Code, JetBrains and the Agent SDK (S3.7); plan, acceptEdits and bypass modes (S3.8); which login each run used (S3.9); Bedrock, Vertex and Foundry (S3.10); `disableAllHooks` and `allowManagedHooksOnly` (S3.13); Linux and Windows installs (S6.1); undocumented binary behaviour beyond macOS (S5.6); no live rerun since 0.1.0 (S9.6).
- Edge cases without a test: inline skills (S3.4); forked skills on the mod path (S3.5); compaction (S12.7); a brief in another language (S12.10); an empty brief, and timing on a very large one (S12.11); two mods setting one spawn (S12.14); a clock moving backwards (S12.20); a missing Python (S12.23, S13.3); a Claude Code rename of an event or field (S12.26); the upstream workflow, which hasn't run on `main` yet (S6.16).
- Not measured: cache sharing across fan-outs (S4.5); hook latency against the timeouts (S4.10); `claude plugin eval` not rerun (S4.11, S9.4).
- Design and process: the cap denial (S2.25); the mod's effort check (S5.2, S13.2); agents with no definition on disk are left alone (S3.6); the user's decision on project raises (S6.4); missing glossary terms (S5.4); no recheck for dead code (S5.5); the changelog's Python claim (S8.2); security testing after each change (S8.3); the dogfood ledger not read (S8.4); no property-based or differential tests (S9.8); no SVG diagrams in the README (S10.13); external citations not audited (S13.6).
