# Benchmark

Measures Model Router's cost per solved task and pass rate against Claude Code without it, for [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). Standard-library Python; nothing here ships with the plugin. Runs need Claude Code's OS sandbox: macOS, Linux or WSL2, not native Windows.

- `tasks/<id>/`: one task each ([Adding a task](#adding-a-task)). `configs.json`: the configurations.
- `run.py`: runs tasks under configurations with `claude -p`, grades each run and appends one JSON row per run to `--out`.
- `analyze.py`: writes `docs/BENCHMARKS.md` from the rows and gives `tools/docs_assets.py` the figures for the `<!-- bench:KEY -->` spans. A row below `run.ROW_SCHEMA` (3) stops it, naming the file and line.
- `overhead.py`: hook time, mod path and state growth, with no paid call: `python3 bench/overhead.py [--runs 30] [--calls 1000] [--json]`.
- `results/`: the `*.jsonl` rows the page judges, `pilot/` for exploratory rounds with a note per batch in `pilot/notes.json`, `limited/` for runs set aside, and `transcripts/`, which git ignores.

## What a run is

Each run copies the task's `repo/` into a scratch folder under `run.RUNS` (`/tmp/mrb-<uid>`) with its own Claude Code config folder (`CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_PROJECT_DIR_NAME=run`), so nothing comes from the machine's settings, plugins or transcripts. A Keychain login can't follow it there, so `run.py` needs `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`), `ANTHROPIC_API_KEY` or a `CLAUDE_CODE_USE_*` switch.

It runs `claude -p <prompt> --output-format stream-json --verbose --model <model> --max-budget-usd <budget> --setting-sources project --strict-mcp-config --permission-mode dontAsk --settings <json>`, then `--plugin-dir` for a router and `--allowedTools Bash Edit(./**)`, plus `Workflow Read(./**)` for a `workflow` task, so the file tools reach only the work dir. The settings run Bash in the OS sandbox (`sandbox.enabled`, `autoAllowBashIfSandboxed`, `allowUnsandboxedCommands: false`, `failIfUnavailable`) with no network, deny `run.CREDENTIAL_FILES` and secret variables, and hide `run.RUNS` (but the run's own folder) and every path in `run.answer_paths`: this checkout, the main checkout and git data, every worktree, `/tmp/claude-<uid>`, the results folder and `--keep`. `permissions.deny` rules `Read(//<path>/**)` and `Edit(//<path>/**)` cover the answer paths. A clone that isn't a worktree is not hidden. Where no sandbox can start (native Windows, or Linux without `bubblewrap` and `socat`), `failIfUnavailable` makes Claude Code exit at startup, so the harness never runs a command unsandboxed: the run is set aside as `no_start` at no cost, and 3 in a row stop the runner.

`CLAUDE_CODE_TMPDIR` is the scratch folder (under about 30 bytes, or Claude Code falls back to the shared one). `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` are set; inherited `CLAUDE_CODE_*`, `CLAUDECODE`, `CLAUDE_PLUGIN_*`, `CLAUDE_PROJECT_DIR`, `PWD` and `OLDPWD` are dropped, except `CLAUDE_CODE_USE_*` and `CLAUDE_CODE_OAUTH_TOKEN`. `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` stays unset: it would strip `CLAUDE_CONFIG_DIR` from the router's hooks. Linux needs `bubblewrap` and `socat`. Managed settings still load; `managed_settings` lists the files and macOS plist found, not server-managed settings or the Windows registry.

A batch loads the router from a copy of `plugins/model-router` made at its start. `--seed` shuffles the run order (random when left out); rows record `order_seed` and `batch` (`--batch`, else the start time and seed), and processes started together share a `--batch`. The prompt hands the task to one subagent with the Agent tool.

`run.grade` runs `check.py` with `python -I` on a copy of the work dir, from a scratch folder under `run.RUNS`, without credential variables (`run.is_secret`); a credential in a file isn't hidden. Anything the check raises is exit 2, a broken check. A socket, pipe or unreadable file is left out of the copy, so it fails the run, as does a check past 120 seconds.

## Configurations

- `plugin`: the router with today's defaults. `"judged": true` makes it the router the tables judge (`analyze.py --router` picks another).
- `plugin-nogate`: the same with `verify_gate` off. Any `"plugin": true` configuration is a router variant: its `plugin_options` go in as `pluginConfigs` in `--settings`, and it gets a column but is never a baseline. The options are `routing_mode`, `max_tier` and `verify_gate`.
- `defaults`: no plugin; subagents inherit the session model.
- `haiku-high`: no plugin; every subagent forced onto Haiku with `CLAUDE_CODE_SUBAGENT_MODEL=haiku` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`, at high effort with `CLAUDE_CODE_SUBAGENT_EFFORT=high` (Claude Code v2.1.257 or later). A fork, and a skill in a subagent with `model: inherit`, still run on the parent's model.

## Row fields

- What ran: `schema`, `task`, `task_revision`, `class`, `shape`, `config`, `batch`, `order_seed`, `run`, `started_at`, `claude_version`, `main_model`, `budget_usd`, `bench_commit`, `bench_dirty`, `plugin_hash`, `managed_settings`.
- The result: `exit_code`, `stopped_by` (`timeout` or `budget` when the harness cut the run short), `subtype`, `is_error`, `api_error_status`, `terminal_reason`, `errors`, `duration_ms`, `duration_api_ms`, `num_turns`, `models`, `prices`, `cache_read_tokens`, `cache_write_tokens` and `fresh_input_tokens` (the whole run's input tokens, subagents and workflow agents included, read from the prompt cache, written to it and sent fresh, summed over the result's `modelUsage`; null when it has none), `answer` (2,000 characters), `stderr` (300, on a non-zero exit), `total_cost_usd`, `cost_known`, `cost_source` (`result`, `transcripts` or `budget`), and `wall_ms`, the runner's clock.
- Agents: `agents_started` (by type, `invalid` for a name that isn't 1 to 64 of letters, digits, `_.:-`), `agent_calls` (`agent`, `named_model`, `named_effort`, and `ran_on`, the model of its first message), `agent_overrides`, `workflow_calls` (`name`, the saved workflow a Workflow call named, null for a script or a path, `invalid` for a name that isn't a plain one, and `is_error`, its tool result's error flag, null when none came back), `permission_denials` (tool and 200 characters of input).
- Router runs: `plugin_loaded`, `options_applied`, `router_silent`, `agent_costs` (spend by agent type, `main` the rest), `routed` (spawn rows: `agent`, `kind` (`subagent`, `workflow` or `teammate`, when the mod routed it), `level`, `model`, `effort`, `source`, `escalated_from`, `on_record` when false), `gate_events`.
- The grade: `grade` (`verdict` `pass`, `fail` or `broken`, `reason`, `check_hash`), `passed`, and `final_answer`, the reply and the files the run added or changed (20,000 characters a file, 80,000 a row).
- Also `claude_started`, `startup_failure_reason`, `kept`, `transcripts` and, set aside, `limited_reason`.

## Runs set aside, and cost

A run that never reached the task or measured the wrong thing goes to `limited/` beside `--out` with a `limited_reason`: `no_start` (costs 0), `limit`, `network`, `api_error` (an error before any spend), `plugin_not_loaded`, `router_silent` (a subagent ran, the ledger has no `spawn` row; also a run with only forks) or `options_not_applied`. A limit or a lost network pauses 1 minute, doubling each time, and retries; after 5 pauses the runner exits 2. Other reasons aren't retried, and 3 in a row exit 2. A failed or denied run keeps its transcripts in `transcripts/` beside the results; read them before trusting a check. `--keep <dir>` copies every run's folder and transcripts.

Every run spends money or usage. Cost is Claude Code's `total_cost_usd`; a run with no result is priced from its transcripts, or charged the whole budget if they can't be priced. `--budget` sets `--max-budget-usd`, which a run can pass by one response plus what running subagents spend. A run that cap or `--timeout` stopped (result subtype `error_max_budget_usd`, or killed) is failed without running its check, whatever its files hold, and its row's `stopped_by` says which; `analyze.py` reads an older row with a null `exit_code` and no `subtype`, or that subtype, the same way, and the page counts stopped runs per configuration, so a cap set too low shows. `--max-total` starts a run or retry only with room for twice that cap, else the runner exits 1.

## Running a round

Smoke-test two `plugin` runs in a folder of their own (each run's sandbox hides the results folder). Both rows need empty `permission_denials`, `plugin_loaded` true and `agent_calls` filled in, and each transcript under `$HOME/mrb-smoke/keep/<run>/session/` should show a sandboxed heredoc and a temp file read back working.

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...   # or ANTHROPIC_API_KEY
python3 bench/run.py --tasks rootcause-cart --configs plugin --runs 2 --seed 1 --batch smoke-1 --budget 1 \
    --max-total 4 --model claude-opus-5-5 --out "$HOME/mrb-smoke/smoke.jsonl" --keep "$HOME/mrb-smoke/keep"
```

A calibration round finds the tasks that tell models apart: the five tasks of work that isn't code and six harder coding tasks, under `haiku-high` and `defaults`, 2 runs each, 44 runs. Rows under `results/pilot/` get no verdict; the page shows each batch as an exploratory round.

```bash
python3 bench/run.py --tasks legal-services-agreement finance-ledger-reconcile finance-runway-forecast \
    research-study-synthesis ops-inbox-triage impl-merge-customers bugfix-statement-queries security-admin-route \
    rootcause-wallet-race cross-amount-cents design-checkout-retries --configs haiku-high defaults --runs 2 --seed 2 \
    --batch calibration-1 --budget 1.5 --max-total 20 --model claude-opus-5-5 \
    --out bench/results/pilot/calibration-1.jsonl
```

Keep a task when `defaults` solves at least 2 of every 3 runs and `haiku-high` at most 1 of every 3 (`analyze.splitting_tasks(rows, "defaults", "haiku-high")`). Two are needed from the 7 candidates outside the 4 fixed coding tasks below; with fewer, swap candidates and run once more. If that fails too, these tasks can't tell Haiku from Opus.

A measurement round runs the 4 fixed tasks and the 2 kept ones under `plugin`, `plugin-nogate`, `defaults` and `haiku-high`, 2 runs each, 48 runs. Verdicts are against `defaults` and `haiku-high`; `plugin-nogate` shows what `verify_gate` adds, and a row set aside with `options_not_applied` means its options didn't arrive. Round 1:

```bash
python3 bench/run.py --tasks bugfix-statement-queries cross-amount-cents design-checkout-retries security-admin-route \
    <kept-1> <kept-2> --configs plugin plugin-nogate defaults haiku-high --runs 2 --first-run 0 --seed 3 \
    --batch measure-1 --budget 1.5 --max-total 25 --model claude-opus-5-5 --out bench/results/measure-1.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check
```

Run five rounds on at least two days, 240 runs; rounds 2 to 5 change only `--first-run` (2, 4, 6, 8), `--seed`, `--batch measure-<n>` and `--out bench/results/measure-<n>.jsonl`. Read the `all tasks` verdicts after round 3 (6 runs per task and configuration) and round 5 (10) only, since each look is another chance for noise to pass. With every run solved, a cost verdict can't show before the second look. Read class rows for a LOSE only. Undecided after round 5 is "no difference shown".

`--timeout` kills a run after that many seconds (default 900), `--model` is the session model where a configuration sets none (default `sonnet`), and `--claude` takes an argv prefix for a stub. Without `--tasks` or `--configs` it runs all. An unknown or repeated id, or a results, `limited/` or `--keep` folder it can't write, exits 2 before any run.

## How a verdict is decided

The rule is fixed before any run. The router gets one verdict per baseline on each row: one per task class, and an `all tasks` row. Its runs and the baseline's are paired by task. Each of 2,000 resamples draws the row's tasks, then each task's runs, with replacement; for the pass rate each task gets one solved and one failed run added, so a cell where every run passed still has spread. The non-inferiority margin is 10 points.

- WIN: the 95% interval of the router's cost per solved task minus the baseline's is below zero, the lower bound of its pass rate minus the baseline's is above minus 10 points, and the router solved at least the baseline's share of runs.
- LOSE: the router's pass rate is behind beyond noise (the upper bound of its pass rate minus the baseline's is below zero, or an exact one-sided test at 5% of its solved runs against the baseline's, every run of the row pooled, puts it behind, which catches a gap the added runs shrink), or it costs more per solved task beyond noise while the upper bound of its pass rate minus the baseline's is below plus 10 points.
- No difference shown: anything else. It is not parity.
- No verdict: the two didn't run each task the same number of times in each batch, Bash was denied in more than a quarter of either's runs, the router's runs loaded more than one `plugin_hash`, the two ran a task at different `budget_usd` or `claude_version` (a task may have its own budget, as long as every configuration ran it at that one), or any of their runs has `bench_dirty`. Nor when the baseline's stopped runs decide it: counted as solved, they would change the verdict; the router's own stopped runs count against it and never withhold one. No verdict, no rank.

A row is LOSE when any baseline beats the router, has no verdict when any comparison has none, and is WIN only against every baseline. A class is saturated when the weakest forced baseline can't be told from the strongest, or from `defaults` when every forced baseline is on one model (`haiku-high` alone), by an exact one-sided test (5%, 10 runs each at least); an equal pass rate there says nothing. Runs on an earlier `revision` of a task, and runs whose check broke, are left out. A row on a task's current revision whose `grade.check_hash` isn't today's `run.check_hash` stops `analyze.py`: the check changed without a raise of `revision`.

One task can't reach WIN: at 10 runs, all solved, its pass-rate interval is -25 to +25 points. On the plan's `all tasks` row, with every run solved and the router cheapest, only 10 runs per task and configuration lift the lower bound above minus 10 points (`tests/test_bench.py`).

## Adding a task

Make `tasks/<id>/` with:

- `task.json`: `class` (one of `run.CLASSES`), `brief`, `revision`, and as needed `revisions` (a line per raise), `shape` (one of `run.SHAPES`), `reference_answer` and `wrong_answer` (for an answer in the reply), `"posix_only": true` (needs POSIX symlinks; the tests skip it on Windows). Raise `revision` whenever a change could change a run's outcome.
- `repo/`, the starting files, and `check.py <workdir> <answer-file>`: exit 0 when solved, 1 when not, 2 only by raising.
- `solution/` (laid over `repo/`) or `reference_answer`.
- `wrong/<name>/` per mistake and `right/<name>/` per fair right answer, laid over `repo/` and `solution/` (`answer.txt` is the reply, `remove.txt` lists paths to delete). A `wrong/` can hold `expect.txt`, a substring the reason must contain.

An answer that is one choice among candidates goes in a JSON file with fixed keys, so a list or a second finding fails. `python3 -m unittest test_bench`, from `tests/`, checks that each check fails on `repo/` and passes on the known-good answer, that every `wrong/` fails (with "the check exited 1" unless it has `expect.txt`) and every `right/` passes, never as broken, and that each task has a `wrong_answer` or a `wrong/`.

A check is deterministic, loads `tasks/passcheck.py` by path and ends with `passcheck.finish(ok, reason)`. Lines that run the work's code sit in `with passcheck.agent_code():`, so whatever that code raises fails the task, while an error in the check exits 2; no check catches `Exception` or `BaseException`. Imported work code could exit 0 for the check, so a new check runs it in a child, a `probe.py` called through `passcheck.probe(work, script, payload)` on fixed or seeded data, and decides from its last JSON line; a probe never holds the answer. `passcheck.tests_pass` runs the work's copy of a shipped test, False if the run changed, removed or hid it, it hangs, or its output isn't UTF-8.

## Task shapes

A `shape` picks another prompt, and each row records it (`single` otherwise): `redo` may run the check the brief names and send the work back, `fanout` gives each question its own subagent at once, `plan-build` hands out the steps in order, `workflow` has the session write a script with one `agent()` stage per step, run it with the Workflow tool and relay what it returns. Accuracy by level counts single-agent rows only, and the redo rate counts runs that started one agent type twice or more. An agent-team shape can't exist: a headless or Agent SDK session never starts teammates, and a named Agent call runs as an ordinary subagent ([agent teams](https://code.claude.com/docs/en/agent-teams.md#enable-agent-teams)).

- `redo-failing-check` (`redo`, the class's only task): its check also refuses a line with a commit hash, a subject from `commits.txt` or a commit-type prefix, which the visible `tools/check_changelog.py` allows. CONTRIBUTING.md leaves out a rule the script enforces, so an answer from the doc fails it, but one that reads the script can pass with no redo. What a line says isn't graded. Measure how often the first try fails before reading the verdict.
- `fanout-three-lookups` (`fanout`).
- `mixed-plan-then-build` (`plan-build`): the check fails a `test_idempotency.py` that passes with any of the six broken builds in `mutants.py`, which wrap the work's own `handle_post`. Its probe counts a charge when the work calls the gateway, even one a rollback erases from `gateway_log`, and retries a request that died right after its first commit. DESIGN.json's reasons need only be non-empty text.
- `workflow-two-stage-report` (`workflow`): the session writes a workflow that finds the version and the upload limit as the code ships them (the README gives stale ones), then writes REPORT.json from them. The models its stages run on are what the run measures: the session's own with no router, and the ones the script names where the router's table is in the Workflow tool's description. The check grades REPORT.json and fails a changed shipped file.

What a row holds for a workflow: its calls in `workflow_calls`, and its agents' tokens and cost in the run's totals (`models`, the three cache counts, `total_cost_usd`). How a workflow agent's messages show in stream-json isn't documented, so `agents_started` and `agent_calls` count Agent calls only and no stage's model is read from the stream. For a router configuration each stage the mod routed is a `routed` row with `kind` `workflow` and the `level`, `model` and `effort` the router gave it, from the router's ledger like the rest of `routed`; Claude Code gives no cost estimate for a workflow agent, so its spend falls in `agent_costs`' `main`. Without the router, no field says which model a stage ran on.

## The hard tasks

`cachefix-stale-across-services` (root-cause), `perf-nlogn-rewrite` and `migrate-ledger-v3` (implementation) and `security-tar-symlink-chain` (security-review) tell a small model from a strong one: 1,000 lines or more over 10 or more files, a brief that gives the goal but not the cause, and a test the agent can run. The check runs that test unchanged and far more, so a wrong answer can pass it and still fail (`HardTasks` in `tests/test_bench.py`). In `calibration-2`, at a $2 cap, each baseline solved 13 of 14 runs: the 900-second timeout stopped one `perf-nlogn-rewrite` run of each, and the dearest run cost $0.99. Read `permission_denials` before a verdict, since each expects its test to run.

```bash
python3 bench/run.py --tasks cachefix-stale-across-services perf-nlogn-rewrite migrate-ledger-v3 \
    security-tar-symlink-chain --configs defaults haiku-high --runs 3 --seed 5 --batch calibration-3 --budget 2 \
    --max-total 60 --model claude-opus-5-5 --out bench/results/pilot/calibration-3.jsonl
```

- Cache: the report blames the order service; the cause is the shared cache's invalidation. Fourteen write orders, each followed by reads that no change touched, which must not reach the database, while the cache is watched during the writes. Limit: one thread, so dropping the cache just before the commit passes.
- Report: 200,000 rows through `reports.render` and `python3 -m reports` (`--out`, `--standings`, `--verify`), each under 10 seconds of CPU; 20,000 rows over 4 seconds end a slow answer early. Small cases must match the shipped code's output and errors on awkward input, including an amount too big for a float, and reuse the same two file paths. Limit: the visible `test_report.py` allows 10 seconds of wall-clock time, so a right answer can fail it on a loaded machine.
- Migration: the check generates an export with every v2 quirk many times over, every currency `ledger/money.py` lists, negative half-hour offsets and moved or retried entries; fails a big export past 15 seconds; keeps amounts past 2**53 minor units exact; and must refuse an amount with too many places and leave no v3 file. Limit: the docs name every quirk, so a careful small model may pass.
- Archive: 24 symlink chains through `safe_extract` and both callers, and 4 normal archives, dangling links included, that must unpack as they are. An audit hook sees every write outside the folder, even one undone before the error, and the links leading out of it before each write. Limits: a child process's writes and undone writes inside the folder aren't seen; no chain escapes only until a later link lands; tarfile's `filter="data"` passes on Python 3.13 and fails on 3.9.6; no chain clashes on case alone.

## Work that isn't code

`legal-services-agreement` (class `document`), `finance-ledger-reconcile` and `finance-runway-forecast` (`analysis`), `research-study-synthesis` (`research`) and `ops-inbox-triage` (`operations`) give the agent documents or data and grade an answer file with fixed keys exactly. Each `wrong/` is a mistake a careless reading makes. The finance checks work their answer out from the shipped `repo/` data by the task's method, never from the agent's copy. `document` and `operations` have one task each, so their verdicts rest on one task.
