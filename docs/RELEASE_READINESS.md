<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><img src="assets/banner-release-readiness.svg" width="880" alt="Release readiness: Is this release ready, and what is still open?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="TRACEABILITY.md">Traceability</a> &nbsp;&middot;&nbsp; Next: <a href="ROADMAP.md">Roadmap</a></p>
<!-- End of page head. -->

# Release readiness

Checked 2026-11-24 for 0.1.2 on Claude Code 2.1.330 and 2.1.331; the evals and the docs review ran on 2.1.331. 0.1.0 was checked 2026-11-23 on the code at `b836649` and the changes after it, up to its release merge `da1e68c`. Row IDs (S2.1 and so on) point into [TRACEABILITY.md](TRACEABILITY.md), which holds the evidence.

**Verdict: 0.1.2 shipped on 2026-11-24 (tag `v0.1.2` on `9e247d3`) by the maintainer's decision with one requirement failing (S4.15), 17 partial and 1 blocked, as 0.1.0 did (tag `v0.1.0` on `da1e68c`).** Of the 13 release gates, 12 pass and the spend ledger is partial. No defect is open. The failing requirement, measured on the routing code at `780ee86`, <!-- bench:per_cell -->5 or 10<!-- /bench --> runs per class and configuration (S4.15): on either session, none of these runs supports a verdict under the benchmark's current rules, because they ran before subagent commands ran in a sandbox and the router ran in other batches than its baselines. On an Opus 5.5 session, Claude Code's default for most accounts, the router cost <!-- bench:opus.pooled.router -->$0.121<!-- /bench --> per solved task over all runs against <!-- bench:opus.pooled.defaults -->$0.151<!-- /bench --> for `defaults`, and `sonnet-via-opusplan` (Sonnet throughout headless) and every subagent on Haiku at `high` effort cost less in every class, since they solve nearly every task here.

## Release gates

| Gate | Status | Evidence |
| :- | :- | :- |
| Every subagent, teammate and workflow stage routed on the mod path | PASS | Unit and Node tests cover all three; all three ran live on 2.1.330, and workflow agents under ultracode on 2.1.331 (S10.1) |
| A working fallback where mods don't load | PASS | Fallback tests, and live runs of the `Agent` call path in default and auto mode (S10.2) |
| The precedence order and the cap honored in every case | PASS | `Router` tests; the cap holds even when a lowering rewrite didn't take (S10.3) |
| Redo escalation and learning that never loop and never lower quality | PASS | `RepeatGuard` and `Feedback` tests; a run cut off by an API error is retried, not escalated (S10.4, S12.33) |
| Availability tracking that routes around a model that doesn't work here | PASS | `Registry` and `Router` tests (S10.5) |
| The daily upstream check, failing quietly offline | PASS | `Upstream` tests for a failed fetch, an empty feed, and a fetch that runs past its deadline (S10.6) |
| A spend ledger that matches list prices | PARTIAL | One-hour cache writes, unpriced models and Haiku's long-prompt rate per request are tested. Fast mode requests are logged at standard rates, because Claude Code doesn't say which ran fast (S10.7, S4.8) |
| Fail-open behaviour proven by tests | PASS | Garbage payloads to every handler; every mod hook falls back to `next(e)` (S10.8) |
| Automated tests on Linux, macOS and Windows, oldest and newest Python | PASS | CI on the 0.1.2 release merge `9e247d3` (run 45361583123): Python 3.9 and 3.13 on all three, 710 tests each, and the mod's Node tests on all three, 30 of 30 (S10.9) |
| CI green on the release PR and on the merge commit in `main`, before tagging | PASS | 0.1.2: PR run 45360301092 on `fc6fb34` and run 45361583123 on the merge `9e247d3` passed before the tag. 0.1.0: run 45088194790 on `da1e68c` (S10.10) |
| Reproducible cost and quality results | PASS | [BENCHMARKS.md](BENCHMARKS.md): <!-- bench:runs -->1,120<!-- /bench --> runs on <!-- bench:tasks -->14<!-- /bench --> of the <!-- bench:tasks_total -->30<!-- /bench --> tasks, <!-- bench:per_cell -->5 or 10<!-- /bench --> per class and configuration, Opus 5.5 and Sonnet sessions, raw data and commands committed, <!-- bench:usd -->$134.97<!-- /bench --> at list price. No class row has a verdict: <!-- bench:opus.status.REFUSED -->8<!-- /bench --> Opus rows and <!-- bench:sonnet.status.REFUSED -->8<!-- /bench --> Sonnet rows have none. Older rows are kept and left out of the tables (S10.11) |
| A security assessment | PASS | [SECURITY.md](SECURITY.md), each claim cited to the code it was checked against (S10.12) |
| Professional documentation and install steps | PASS | The README has its SVG diagrams, and the changelog's 0.1.0 section names the Python versions CI ran (S10.13, S8.2) |

## Remaining defects and incomplete requirements

### Open defects

None.

### Decisions needed

- Fast mode in the spend log: Claude Code's own estimate in `/cost` includes fast mode, and a mod can read it. Logging it beside the router's list-price figure would change the ledger and the usage report. Rows S4.8, S10.7.
- Settled: the ladder. On the first builds (rows under `bench/results/archive/`) the router cost 1.2 to 2.2 times the best baseline per solved task in every class but lookup on a Sonnet session, and `sonnet-medium` solved implementation and known-cause bugfix tasks as often as `sonnet-high` for less ($0.071 against $0.081). So standard runs on Sonnet at `medium` and `main_effort` is off by default. The quality-first review then moved complex to Opus at `high`; hardest runs on Opus at `xhigh` (`docs/research/quality-first-routing.md`). Rows S4.15, S10.11.
- Settled: `verify_gate` is on by default, since the cheap first pass relies on a check to catch a weak result (`docs/research/quality-first-routing.md`, recommendation 1).
- Found on an Opus 5.5 session (<!-- bench:runs -->1,120<!-- /bench --> runs in all, both sessions): for the router at `780ee86`, none of these runs supports a verdict under the benchmark's current rules: they ran before subagent commands ran in a sandbox, and the router ran in other batches than its baselines. Over all runs, `sonnet-via-opusplan` cost <!-- bench:opus.pooled.sonnet-via-opusplan -->$0.069<!-- /bench --> per solved task (<!-- bench:opus.solved.sonnet-via-opusplan -->69 of 70<!-- /bench --> solved), every subagent on Haiku <!-- bench:opus.pooled.haiku -->$0.080<!-- /bench --> (<!-- bench:opus.solved.haiku -->66 of 70<!-- /bench -->) and on Haiku at `high` <!-- bench:opus.pooled.haiku-high -->$0.075<!-- /bench --> (<!-- bench:opus.solved.haiku-high -->70 of 70<!-- /bench -->), against the router's <!-- bench:opus.pooled.router -->$0.121<!-- /bench -->. `sonnet-via-opusplan` and `haiku-high` solve nearly every task in this set, so the router's Sonnet and Opus picks cost more with no pass-rate gain to show. Rows S4.15, S14.1.
- Found at `780ee86`: research got cheaper on both sessions (Sonnet $0.085 per solved task against $0.126 at `27b94e6`; Opus $0.123 against $0.150, still FAIL against `haiku-high`), since only research-grade wording reaches Fable. On Sonnet the other classes are within the noise of `27b94e6` and still behind: the router writes more cache tokens per run than `defaults` on the same model (1,400 to 5,200 more at `27b94e6`, which fits the context the plugin adds), runs security review at complex, and sent some design tasks to the read-only architect, which then needed an implementer that routed to hardest on Opus or ended without its file. `89fe183` rewrote the architect's description, and a rerun of the two design tasks on Sonnet started it 0 of 7 times. On Opus, design is level because 7 of 10 design briefs route to hardest, the same Opus `defaults` uses. Next: the harder task set and the `fast` priority. Rows S4.15, S4.12.
- Found at `27b94e6`, fixed at `780ee86` (the bare word is gone from `_FRONTIER` in `scripts/modelrouter/profile.py`): once a brief reached hardest, the bare word "research" moved it to frontier, which may explain 2 of 10 research runs on Fable; the rows don't keep the brief. No spawn escalated (0 of 141). Rows S4.15, S4.12.
- On builds before `27b94e6` (rows under `bench/results/archive/early-builds/`): on a Sonnet session the cheap ladder with `main_effort` and `verify_gate` off still cost 10 to 25% more per solved task than `defaults` in the 7 classes it lost (2,300 more cache-write and 11,000 more cache-read tokens per run, before `f9b9a1b` cut the plugin's context). On an Opus 5.5 session, `plugin-m6-08-b-nogate` cost $0.123 per solved task against $0.156 for `defaults` (95% interval of the difference -$0.048 to -$0.019); with the gate on it was level, since the gate adds 1.15 main-loop turns per run. `design-cache-invalidation` on the Sonnet `implementer` passed 1 of 4 times on Opus against 4 of 4 for `defaults`, because the first spec didn't say whether the bus can drop an event (`62e2a9c` settled it). Rows S4.15, S10.11.
- Baselines the benchmark still leaves out: every subagent on Sonnet on an Opus 5.5 session, and the Haiku, Opus and Fable baselines on a Sonnet session. Row S14.1.
- Whether the Mythos models become a routable family. They are priced in the registry, so their spend is logged, but no level routes to them. Row S2.15.
- A live install check: installing from the marketplace into a scratch config folder and running one routed call. A scratch config folder has its own login, so the run needs the maintainer's login or an API key. Row S6.1.
- Requirement against design: the requirements say the router never blocks a spawn. On the fallback path, a repeated call whose untouched model would break the user's cap is denied, with the model to use (`docs/ARCHITECTURE.md`, "Rewrites that don't take"). Either the requirements make an exception for the cap or the design changes. Row S2.25.
- The mod's one decision: `turn.step` compares effort ranks so the main loop's effort only rises. The requirements say the mod holds no routing logic. Rows S5.2, S13.2.

### Blocked

- A fast-mode price per request: fast mode keeps the model ID, and the per-request usage Claude Code gives the mod has no speed field. Row S4.8.

### Not implemented (FAIL)

- Beating every baseline on cost per solved task: on the routing code at `780ee86`, no class is a win against the best baseline on an Opus 5.5 session (`opusplan` and every subagent on Haiku at `high` effort cost less in every class), and no Sonnet row is PASS. Row S4.15.

### Incomplete or not verified enough (PARTIAL)

- No live run: Desktop, VS Code, JetBrains and the Agent SDK (S3.7); an API-key login, which needs a key (S3.9); Bedrock, Vertex and Foundry (S3.10); `allowManagedHooksOnly` (S3.13); Linux and Windows installs (S6.1); undocumented binary behaviour beyond macOS (S5.6).
- Edge cases without a test or with a known gap: a short brief, or a language outside the profile's list (S12.10); on the mod path, a spawn another plugin's mod moves to another family counts as a substitution (S12.14); the upstream workflow, whose scheduled runs on `main` passed but have not yet flagged a real change (S6.16).
- Not measured: fan-out, workflow, team and redo shapes, cache temperature and `priority` (S4.13).
- Design and process: the cap denial (S2.25); the mod's effort check (S5.2, S13.2); agents with no definition on disk are left alone (S3.6); fast mode in the spend ledger (S10.7).
