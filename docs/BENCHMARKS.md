<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="CLAUDE_CODE_DOCS.md">Built on the docs</a> &nbsp;&middot;&nbsp; Next: <a href="TESTING.md">Testing</a></p>
<!-- End of page head. -->

# Benchmarks

Cost and quality of Model Router against Claude Code without it. No judged round has been recorded yet. The exploratory rounds are at the end of this page.

## How to read it

- A task counts as solved only when its pass check passes. A wrong answer is a failure, the router's too.
- A run the harness stopped, by the timeout or by the budget cap (`--max-budget-usd`), counts as failed whatever its files held, the router's too; its raw line's `stopped_by` says which. There is no verdict against a baseline when its stopped runs decide it: counted as solved, they would give the router another verdict. The router's own stopped runs count against it and never withhold a verdict.
- Cost per solved task is a configuration's total spend on a task class divided by the runs it solved, so failed runs count against it.
- The cells show each configuration's own figures: exact 95% binomial (Clopper-Pearson) pass-rate intervals, and 95% percentile bootstrap intervals of cost per solved task over runs (2,000 resamples, each cell from a generator seeded by its session model, class and configuration). No verdict rests on them.
- Verdicts, one per baseline, by a rule fixed before any run. The router's runs and the baseline's are paired by task. Each of 2,000 resamples draws the row's tasks with replacement, then each drawn task's runs with replacement, so the interval reflects how few tasks there are as well as how few runs. For the pass rate, each task's runs get one solved and one failed run added, so a cell where every run passed still has spread. The non-inferiority margin is 10% of pass rate. WIN: the 95% interval of the router's cost per solved task minus the baseline's is below zero, the lower bound of its pass rate minus the baseline's is above minus 10%, and the router solved at least the baseline's share of runs (per-task pass rates averaged), so a saving counts only at an equal or higher pass rate. LOSE: the router's pass rate is behind beyond noise (the upper bound of its pass rate minus the baseline's is below zero, or an exact one-sided test at 5% of its solved runs against the baseline's, every run of the row pooled, puts it behind, which catches a gap the added runs shrink), or its cost per solved task is above beyond noise while the upper bound of its pass rate minus the baseline's is below plus 10%, the mirror of the WIN rule. A cost bound that is n/a decides nothing: it comes from resamples in which one side solved none of the runs drawn, so it has no cost per solved task, and a row where a side solved nothing, or nearly nothing, gets its verdict from the pass rate alone. Anything else is "no difference shown", which is not parity. There is no verdict when the two didn't run each task the same number of times in each batch, since each side's cost is pooled over its runs and pass rates drift between batches, when more than 25% of either's runs had a Bash call denied, when the router's runs loaded more than one plugin build (`plugin_hash`), when the two ran a task at different budgets (`budget_usd`) or on different Claude Code versions (`claude_version`), or when any of their runs ran from code with uncommitted changes (`bench_dirty`). A row is LOSE when any baseline beats the router, has no verdict when any comparison has none, is WIN only when it wins against every baseline, and is otherwise "no difference shown".
- Each table ends with an `all tasks` row, judged the same way over every task in the table: the tasks of all its classes are paired and resampled together. A class with one task can't show the margin, so a plan with a few tasks per class is judged on its `all tasks` row.
- A class is saturated when `haiku-high` (every subagent on Haiku) can't be told from `defaults` (subagents on the session model) by an exact one-sided test (5%, 10 runs each at least). Then an equal pass rate says nothing about quality. Runs on an earlier revision of a task are left out of every table. A run on a task's current revision that a check other than today's graded stops the page from being built, since the check changed without a raise of the revision.
- Rank is the router's place among the baselines by cost per solved task, 1 being cheapest, and n/a on a row with no verdict; a configuration that solved none of the row's runs counts as the dearest. A router variant (another configuration that loads the plugin) has its own column but is never a baseline.
- Run metrics, per class and configuration: p50 and p90 are nearest-rank percentiles of each run's wall time (`wall_ms`, the runner's clock around the `claude` process). Cache is cache-read tokens over all input tokens, which are input plus cache reads plus cache writes (https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md), summed over every model that ran. Bash denied counts the runs whose result lists a denied Bash call. Model named counts the Agent calls in the runs' streams that set their own `model`, and effort the ones that set `effort` (`agent_calls`, which also holds the model each call's first message ran on): Model Router keeps what a call names, and a call that names no model stays on its parent's. Redo, for a task with the redo shape, counts the runs that started one agent type twice or more (`agents_started`). It is an estimate: a second agent of that type that only ran the check counts too, and work sent back as a message to the same subagent doesn't. For a router configuration, accuracy by level is the pass rate of its single-agent runs grouped by the level the router gave the run's first subagent. Escalated counts the spawns whose ledger row names the level of a run they redo (`escalated_from`, unless `on_record` is false, since then no run at that level is on record), over the spawns whose row says either way. Both come from the run's `routed` line, and n/a means no run in the cell has it.

## Configurations

- `plugin`: Model Router with today's defaults, loaded with --plugin-dir from a copy frozen at the start of the batch
- `plugin-nogate`: Model Router with the Stop check off (`verify_gate: false`)
- `defaults`: No plugin: subagents inherit the session model at its default effort
- `haiku-high`: No plugin: every subagent on haiku at high effort (CLAUDE_CODE_SUBAGENT_MODEL=haiku, CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1, CLAUDE_CODE_SUBAGENT_EFFORT=high)

## Commands

```bash
python3 bench/run.py --tasks <ids> --configs <ids> --runs <n> --budget 0.5 --max-total <usd> --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

## Exploratory rounds

Small rounds run to choose tasks and settings. They get no verdict: each has too few runs of a configuration for one, and a router configuration here may run the plugin from another commit. A run the harness stopped, by the timeout or the budget cap, counts as failed whatever its files held. As in the judged tables, runs on an earlier revision of a task, runs whose pass check broke and runs the bench stopped before their check finished are left out.

### `calibration-1`: 28 runs on 2026-11-25, $5.27, session on `claude-opus-5-5`

Eleven tasks under the two baselines, to look for tasks that tell a strong subagent from a cheap one. None did: both solved every run. The table counts ten of them, since `ops-inbox-triage` has changed since it ran.

Left out of the table: 16 run(s) ($3.94) on an earlier revision of `bugfix-statement-queries`, `impl-merge-customers`, `ops-inbox-triage`, `rootcause-wallet-race`.

| Configuration | Runs | Solved | Stopped | Cost per run | Plugin commit |
| :- | -: | -: | :- | -: | :- |
| `defaults` | 14 | 14 | none | $0.249 |  |
| `haiku-high` | 14 | 14 | none | $0.127 |  |

### `calibration-2`: 24 runs on 2026-11-25, $8.37, session on `claude-opus-5-5`

The four hard coding tasks and the redo, fan-out and plan-then-build tasks under the same two baselines. Each solved 13 of 14 runs: the one it missed was a `perf-nlogn-rewrite` run the 900-second timeout stopped.

Left out of the table: 4 run(s) ($1.50) on an earlier revision of `mixed-plan-then-build`.

| Configuration | Runs | Solved | Stopped | Cost per run | Plugin commit |
| :- | -: | -: | :- | -: | :- |
| `defaults` | 12 | 11 | 1 by the timeout | $0.517 |  |
| `haiku-high` | 12 | 11 | 1 by the timeout | $0.180 |  |

### `release-1`: 66 runs on 2026-11-25, $15.78, session on `opus`

Sixteen tasks under the plugin and the two baselines, with the ladder and the table the plugin ships. It stays exploratory for two reasons: the plugin has changed since the commit it ran, and each run had a $0.50 cap, which stopped five runs before they finished.

Left out of the table: 30 run(s) ($6.83) on an earlier revision of `bugfix-statement-queries`, `design-cache-invalidation`, `impl-merge-customers`, `mixed-plan-then-build`, `rootcause-wallet-race`.

| Configuration | Runs | Solved | Stopped | Cost per run | Plugin commit |
| :- | -: | -: | :- | -: | :- |
| `defaults` | 22 | 19 | 3 by the budget cap | $0.347 |  |
| `plugin` | 22 | 21 | 1 by the budget cap | $0.225 | `99467a2` |
| `haiku-high` | 22 | 22 | none | $0.146 |  |
