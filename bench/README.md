# Benchmark

Measures Model Router's cost per solved task and pass rate against Claude Code without it, for [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). Standard-library Python; nothing here ships with the plugin. Runs need Claude Code's OS sandbox: macOS, Linux or WSL2, not native Windows.

- `tasks/<id>/`: one task each ([Adding a task](#adding-a-task)). `configs.json`: the configurations. `prices.json`: list prices by model.
- `run.py`: runs tasks under configurations with `claude -p`, grades each run and appends one JSON row per run to `--out`. `--help` lists its options.
- `analyze.py`: writes `docs/BENCHMARKS.md` from the rows. A row below `run.ROW_SCHEMA` (3) stops it, naming the file and line.
- `overhead.py`: hook time, mod path and state growth, with no paid call: `python3 bench/overhead.py [--runs 30] [--calls 1000] [--json]`.
- `results/`: the `*.jsonl` rows the page judges, `pilot/` for exploratory rounds with a note per batch in `pilot/notes.json`, `limited/` for runs set aside, and `transcripts/`, which git ignores.

## What a run is

Each run copies the task's `repo/` into a scratch folder under `run.RUNS` (`/tmp/mrb-<uid>`) with a Claude Code config folder of its own (`CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_PROJECT_DIR_NAME=run`), so no settings, plugins or transcripts come from the machine. A Keychain login can't follow it there, so `run.py` needs `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`), `ANTHROPIC_API_KEY` or a `CLAUDE_CODE_USE_*` switch in the environment, and stops before any run without one. With both set, the API key pays ([authentication precedence](https://code.claude.com/docs/en/authentication.md#authentication-precedence)), so unset the one that shouldn't; each row's `auth` and the page's Setup say which paid.

It runs `claude -p <prompt> --output-format stream-json --verbose --model <model> --max-budget-usd <budget> --setting-sources project --strict-mcp-config --permission-mode dontAsk --settings <json>`, then `--plugin-dir` for a router and `--allowedTools Bash Edit(./**)`, plus `Workflow Read(./**)` for a `workflow` task, so the file tools reach only the work dir. The settings run Bash in the OS sandbox (`sandbox.enabled`, `autoAllowBashIfSandboxed`, `allowUnsandboxedCommands: false`, `failIfUnavailable`) with no network, deny `run.CREDENTIAL_FILES` and every secret variable, and hide `run.RUNS` (but the run's own folder) and `run.answer_paths`: this checkout, the main checkout and git data, every worktree, `/tmp/claude-<uid>`, the results folder and `--keep`. `permissions.deny` rules `Read(//<path>/**)` and `Edit(//<path>/**)` keep the file tools out of the answer paths. A clone that isn't a worktree is not hidden. Where no sandbox can start (native Windows, or Linux without `bubblewrap` and `socat`), `failIfUnavailable` makes Claude Code exit at startup, so the harness never runs a command unsandboxed: the run is set aside as `no_start` at no cost.

`CLAUDE_CODE_TMPDIR` is the scratch folder, kept under about 30 bytes so Claude Code doesn't fall back to the shared one. `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` are set. Inherited `CLAUDE_CODE_*`, `CLAUDECODE`, `CLAUDE_PLUGIN_*`, `CLAUDE_PROJECT_DIR`, `PWD` and `OLDPWD` are dropped, except `CLAUDE_CODE_USE_*` and `CLAUDE_CODE_OAUTH_TOKEN`. Managed settings still load: `managed_settings` lists the files and macOS plist found, but can't see server-managed settings or the Windows registry. Each `run.py` process loads the router from a copy of `plugins/model-router` made when it starts. The prompt hands the task to one subagent with the Agent tool, unless the task has a [shape](#task-shapes).

`run.grade` runs `check.py` with `python -I` on a copy of the work dir, from a scratch folder under `run.RUNS`, without credential variables, in `sandbox-exec` on macOS or `bwrap` on Linux: no network, `run.CREDENTIAL_FILES` unreadable, writes only to the scratch folder. `grade.sandbox` names which. Limit: where neither can start (native Windows, Linux without `bubblewrap` or barring unprivileged namespaces, a bench run inside another macOS sandbox), the check and the work's code it runs are graded without a sandbox, as the user, and `grade.sandbox` is null. Anything the check raises is exit 2, a broken check. A socket, pipe or unreadable file is left out of the copy, so it fails the run, as does a check past 120 seconds.

## Configurations

- `plugin`: the router with its default options. `"judged": true` makes it the router the tables judge (`analyze.py --router` picks another). Another `"plugin": true` configuration would be a router variant: it gets a column but is never a baseline.
- `defaults`: no plugin; subagents inherit the session model.
- `haiku-high`: no plugin; every subagent on Haiku at high effort (`CLAUDE_CODE_SUBAGENT_MODEL=haiku`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`, `CLAUDE_CODE_SUBAGENT_EFFORT=high`). A fork still runs on the session model.

## Row fields

- What ran: `schema`, `task`, `task_revision`, `class`, `shape`, `config`, `batch`, `order_seed`, `run`, `started_at`, `claude_version`, `auth` (the `apiKeySource` of the run's `system/init` event: `ANTHROPIC_API_KEY` for an API key, `none` for a login token, a bearer token or a cloud provider; null when the stream never began), `main_model`, `budget_usd`, `bench_commit`, `bench_dirty`, `plugin_hash`, `managed_settings`.
- The result: `exit_code`, `stopped_by` (`timeout` or `budget`), `subtype`, `is_error`, `api_error_status`, `terminal_reason`, `errors`, `duration_ms`, `duration_api_ms`, `num_turns`, `models`, `prices` (from `prices.json`), `cache_read_tokens`, `cache_write_tokens` and `fresh_input_tokens` (the whole run's, subagents and workflow agents included, from `modelUsage`), `answer` (2,000 characters), `stderr` (300, on a non-zero exit), `total_cost_usd`, `cost_known`, `cost_source` (`result`, `transcripts` or `budget`), and `wall_ms`, the runner's clock.
- Agents: `agents_started` (by type), `agent_calls` (`agent`, `named_model`, `named_effort`, and `ran_on`, the model of its first message), `agent_overrides`, `workflow_calls` (`name`, null for a script or a path, and `is_error`), `permission_denials` (tool and 200 characters of input).
- Router runs: `plugin_loaded` (null when the stream never began), `router_silent`, `agent_costs` (spend by agent type, `main` the rest), `routed` (spawn rows: `agent`, `kind`, `level`, `model`, `effort`, `source`, `escalated_from`, `on_record` when false), `gate_events`.
- The grade: `grade` (`verdict` `pass`, `fail`, `broken`, or `stopped` when the bench itself stopped the run before its check finished; `reason`, `check_hash`, `sandbox`), `passed`, and `final_answer`, the reply and the files the run added or changed (20,000 characters a file, 80,000 a row).
- Also `claude_started`, `startup_failure_reason`, `kept`, `transcripts` and, set aside, `limited_reason`.

## Runs set aside, and cost

A run that never reached the task or measured the wrong thing goes to `limited/` beside `--out` with a `limited_reason`: `no_start` (costs 0), `limit`, `network`, `api_error` (an account error, set aside whatever it spent: a 401, 402 or 403, or Claude Code's message for no credit, a spend or usage limit, a bad or revoked key or login, a disabled organization or account, or a model the account may not use; or any other error before any spend), `plugin_not_loaded` or `router_silent` (a subagent ran, the ledger has no `spawn` row; also a run with only forks). A limit or a lost network pauses 1 minute, doubling each time, and retries; after 5 pauses the runner exits 2. Other reasons aren't retried, and 3 in a row exit 2. A failed or denied run keeps its transcripts in `transcripts/` beside the results; read them before trusting a check. `--keep <dir>` copies every run's folder and transcripts.

Every run spends money or usage. Cost is Claude Code's `total_cost_usd`; a run with no result is priced from its transcripts, or charged the whole budget if they can't be priced. `--budget` sets `--max-budget-usd`, which a run can pass by one response plus what running subagents spend. A run that cap or `--timeout` stopped is failed without running its check, whatever its files hold, and the page counts stopped runs per configuration, so a cap set too low shows. `--max-total` starts a run or retry only with room for twice that cap, else the runner exits 1.

## Running a round

Smoke-test two `plugin` runs in a folder outside `bench/results/`, so the page never counts them. Both rows need empty `permission_denials`, `plugin_loaded` true and `agent_calls` filled in, and each transcript under `$HOME/mrb-smoke/keep/<run>/session/` should show a sandboxed heredoc and a temp file read back working.

```bash
python3 bench/run.py --tasks rootcause-cart --configs plugin --runs 2 --seed 1 --batch smoke-1 --budget 1 \
    --max-total 4 --model claude-opus-5-5 --out "$HOME/mrb-smoke/smoke.jsonl" --keep "$HOME/mrb-smoke/keep"
```

Rows under `results/pilot/` get no verdict: the page shows each batch as an exploratory round, leaving out what the judged tables leave out and saying how many.

### The judged plan

This plan is fixed before any run. Each batch is read once, when all its runs are in, since every look is another chance for noise to pass. Both batches ran on 2026-11-25, and [docs/BENCHMARKS.md](../docs/BENCHMARKS.md) has the result.

Batch 1, on an Opus session: the 16 tasks below under `plugin`, `defaults` and `haiku-high`, 5 runs of each task under each, 240 runs, as five processes of 48 side by side, each its own run number, with a $2 cap per run. Each process's total cap lets every run start even if all of them hit that cap. Run a batch on a machine doing nothing else heavy: `migrate-ledger-v3` fails a big export that takes over 15 seconds, and its reference answer takes about 2 seconds with five copies at once, so a very busy machine can fail a right answer.

```bash
for n in 0 1 2 3 4; do
  python3 bench/run.py --tasks rootcause-wallet-race cachefix-stale-across-services impl-merge-customers \
      finance-ledger-reconcile legal-services-agreement fanout-three-lookups design-cache-invalidation \
      security-tar-symlink-chain redo-failing-check workflow-two-stage-report mixed-plan-then-build \
      research-study-synthesis ops-inbox-triage migrate-ledger-v3 bugfix-statement-queries design-checkout-retries \
      --configs plugin defaults haiku-high --runs 1 --first-run "$n" --seed "$n" --batch judged-opus --budget 2 \
      --max-total 100 --model claude-opus-5-5 --out "bench/results/judged-opus-$n.jsonl" &
done
wait
```

Batch 2, on a Fable session: the 4 tasks below under the same three configurations, 5 runs each, 60 runs, as five processes of 12 with the same $2 cap.

```bash
for n in 0 1 2 3 4; do
  python3 bench/run.py --tasks rootcause-wallet-race impl-merge-customers fanout-three-lookups design-cache-invalidation \
      --configs plugin defaults haiku-high --runs 1 --first-run "$n" --seed "$n" --batch judged-fable --budget 2 \
      --max-total 28 --model claude-fable-5-2 --out "bench/results/judged-fable-$n.jsonl" &
done
wait
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

To stop a batch, send its processes SIGTERM (`pkill -f bench/run.py`): each kills its `claude` and writes the row of the run it paid for. A process that stopped (at a limit it couldn't wait out, after 3 runs in a row set aside, or by a signal) leaves gaps, and a verdict needs none, so run the whole loop again. The same command skips each run number, task and configuration already in its `--out`, runs again one the bench stopped, set aside or ran on an earlier `revision` of its task, and counts what its `--out` and `limited/` rows cost toward `--max-total`. Run it on the same Claude Code and plugin: a process whose file holds a run on another `claude_version`, or a router run on another `plugin_hash`, exits 2 before any run, since the page gives no verdict over two versions or two plugin builds. After a Claude Code update, finish the batch with `--claude ~/.local/share/claude/versions/<version>`, the version the error names; after a plugin change, check out the commit in the rows' `bench_commit`. A process started from a tree with uncommitted changes under `bench/` or `plugins/` prints one warning line and runs, but the page gives those rows no verdict (`bench_dirty`), so commit first.

Each session model gets a table of its own, and the verdicts read are each table's `all tasks` rows; a class row has too few tasks to reach WIN, so it is read for a LOSE only. Read each `all tasks` row with its line under "Do the tasks tell models apart": on a saturated row a WIN is a cost result and says nothing about quality. At 5 runs of each of 16 tasks, every run solved, the router's pass rate minus a baseline's has a 95% interval of about minus 9 to plus 9 points, so a WIN and a cost LOSE can both fire; at 2 runs it is about minus 15 to plus 15, and neither could. Batch 2's 4 tasks at 5 runs give about minus 18 to plus 18: it can show a pass-rate LOSE, never a WIN or a cost LOSE (`tests/test_bench.py`). Each outcome is reported this way:

- WIN against both baselines: Model Router is cheaper per solved task than Claude Code's defaults and than every subagent forced onto Haiku, at a pass rate shown no more than 10 points behind each.
- WIN against one, cost LOSE against the other: cheaper per solved task than the first and dearer than the second, at a pass rate shown no more than 10 points behind each. Against `haiku-high`, the outcome the exploratory rounds point to, it is reported as a loss: forcing every subagent onto Haiku costs less per solved task, at a pass rate not shown 10 points worse.
- LOSE on pass rate against either: Model Router solves fewer tasks than that baseline, whatever it costs.
- No difference shown against a baseline: no cost or quality claim against it, and never "as good as".
- No verdict: the reason the page gives, and no claim.
- Batch 2, with no pass-rate LOSE: no difference shown on a Fable session; its costs are reported as measured, with no claim.

Other options: `--timeout` (default 900 seconds) kills a run and every process it started, since `claude` runs in a session of its own (on Windows, `claude` alone); a timeout, Ctrl-C or SIGTERM still writes the run's row. `--model` is the session model of every configuration (default `sonnet`). `--seed` shuffles the run order (random when left out); processes started together share one `--batch` (default: the start time and seed). `--claude` takes an argv prefix, such as a stub. Its first word is resolved through any symlink once, at start, so an update during a round can't reach a later run; `claude_version` is that file's. Without `--tasks` it runs every task, and without `--configs` the three configurations, `plugin`, `defaults` and `haiku-high`. An unknown or repeated id, a results, `limited/` or `--keep` folder it can't write, or, with runs left to do, a row on record on another `claude_version` or `plugin_hash`, exits 2 before any run.

## How a verdict is decided

The rule is fixed before any run. The router gets one verdict per baseline on each row: one per task class, and an `all tasks` row. Its runs and the baseline's are paired by task. Each of 2,000 resamples draws the row's tasks, then each task's runs, with replacement; for the pass rate each task gets one solved and one failed run added, so a cell where every run passed still has spread. The non-inferiority margin is 10 points.

- WIN: the 95% interval of the router's cost per solved task minus the baseline's is below zero, the lower bound of its pass rate minus the baseline's is above minus 10 points, and the router solved at least the baseline's share of runs (per-task pass rates averaged).
- LOSE: the router's pass rate is behind beyond noise (the upper bound of its pass rate minus the baseline's is below zero, or an exact one-sided test at 5% of its solved runs against the baseline's, every run of the row pooled, puts it behind), or it costs more per solved task beyond noise while the upper bound of its pass rate minus the baseline's is below plus 10 points.
- A cost bound that is n/a, from resamples in which one side solved none of the runs drawn, decides nothing. A row where a side solved nothing, or nearly nothing, gets its verdict from the pass rate alone.
- No difference shown: anything else. It is not parity.
- No verdict: the two didn't run each task the same number of times in each batch, Bash was denied in more than a quarter of either's runs, the router's runs loaded more than one `plugin_hash`, the two ran a task at different `budget_usd` or `claude_version`, or any of their runs has `bench_dirty`. Nor when the baseline's stopped runs decide it: counted as solved, they would change the verdict. The router's own stopped runs count against it. No verdict, no rank.

A row is LOSE when any baseline beats the router, has no verdict when any comparison has none, and is WIN only against every baseline. A row, the `all tasks` row too, is saturated when an exact one-sided test (5%, at least 10 runs of each) doesn't show `haiku-high` solving fewer runs than `defaults`; an equal pass rate there says nothing about quality, a WIN there is a cost result, and the page's header counts the saturated rows. Runs on an earlier `revision` of a task, runs whose check broke and runs the bench stopped before their check finished are left out. A row on a task's current revision whose `grade.check_hash` isn't the task's current `run.check_hash` stops `analyze.py`: the check changed without a raise of `revision`.

## Adding a task

Make `tasks/<id>/` with:

- `task.json`: `class` (one of `run.CLASSES`), `brief`, `revision`, and as needed `revisions` (a line per raise), `shape` (one of `run.SHAPES`), `reference_answer` and `wrong_answer` (for an answer in the reply), `"posix_only": true` (needs POSIX symlinks; the tests skip it on Windows). Raise `revision` whenever a change could change a run's outcome.
- `repo/`, the starting files, and `check.py <workdir> <answer-file>`: exit 0 when solved, 1 when not, 2 only by raising.
- `solution/` (laid over `repo/`) or `reference_answer`.
- `wrong/<name>/` per mistake and `right/<name>/` per fair right answer, laid over `repo/` and `solution/` (`answer.txt` is the reply, `remove.txt` lists paths to delete). A `wrong/` can hold `expect.txt`, a substring the reason must contain.

An answer that is one choice among candidates goes in a JSON file with fixed keys, so a list or a second finding fails. `python3 -m unittest test_bench`, from `tests/`, checks that each check fails on `repo/` and passes on the known-good answer, that every `wrong/` fails (with "the check exited 1" unless it has `expect.txt`) and every `right/` passes, never as broken, and that each task has a `wrong_answer` or a `wrong/`.

A check is deterministic, loads `tasks/passcheck.py` by path and ends with `passcheck.finish(ok, reason)`. It reads a JSON answer with `passcheck.answer` or `passcheck.finding`, or catches `ValueError` and `RecursionError` itself, so JSON nested too deep fails the run instead of breaking the check. Lines that run the work's code sit in `with passcheck.agent_code():`, so whatever that code raises fails the task, while an error in the check exits 2; no check catches `Exception` or `BaseException`. A new check runs the work's code in a child, a `probe.py` called through `passcheck.probe(work, script, payload)` on fixed or seeded data, and decides from its last JSON line, since imported code could exit 0 for the check. A probe never holds the answer. `passcheck.tests_pass` runs the work's copy of a shipped test, False if the run changed, removed or hid it, it hangs, or its output isn't UTF-8.

## Task shapes

A `shape` picks another prompt, and each row records it (`single` otherwise): `redo` may run the check the brief names and send the work back, `fanout` gives each question its own subagent at once, `plan-build` hands out the steps in order, `workflow` has the session write a script with one `agent()` stage per step, run it with the Workflow tool and relay what it returns. Accuracy by level counts single-agent rows only, and the redo rate counts runs that started one agent type twice or more. An agent-team shape can't exist: a headless or Agent SDK session never starts teammates ([agent teams](https://code.claude.com/docs/en/agent-teams.md#enable-agent-teams)).

- `redo-failing-check` (`redo`, the class's only task): the visible `tools/check_changelog.py` enforces a rule CONTRIBUTING.md leaves out, so an answer from the doc fails it, but one that reads the script can pass with no redo. The check also refuses a commit hash, a pasted commit subject or a commit-type prefix, which the script allows, and doesn't grade what a line says. Measure how often the first try fails before reading the verdict.
- `fanout-three-lookups` (`fanout`).
- `mixed-plan-then-build` (`plan-build`): the check fails a `test_idempotency.py` that passes with any of the six broken builds in `mutants.py`. DESIGN.json's reasons need only be non-empty text.
- `workflow-two-stage-report` (`workflow`): the session writes a workflow that finds the version and the upload limit as the code ships them (the README gives stale ones), then writes REPORT.json from them. The check grades REPORT.json and fails a changed shipped file.

For a workflow, `workflow_calls` holds its calls, and the run's totals (`models`, the three cache counts, `total_cost_usd`) include its agents. `agents_started` and `agent_calls` count Agent calls only, since stream-json doesn't document a workflow agent's messages. For a router configuration each stage the mod routed is a `routed` row with `kind` `workflow` and the `level`, `model` and `effort` the router gave it, and its spend falls in `agent_costs`' `main`, since Claude Code gives no cost estimate for a workflow agent. Without the router, no field says which model a stage ran on.

## The hard tasks

`cachefix-stale-across-services` (root-cause), `perf-nlogn-rewrite` and `migrate-ledger-v3` (implementation) and `security-tar-symlink-chain` (security-review) tell a small model from a strong one: 1,000 lines or more over 10 or more files, a brief that gives the goal but not the cause, and a test the agent can run. The check runs that test unchanged and far more, so a wrong answer can pass it and still fail (`HardTasks` in `tests/test_bench.py`). In `calibration-2`, at a $2 cap, each baseline solved 13 of 14 runs: the 900-second timeout stopped one `perf-nlogn-rewrite` run of each, and the dearest run cost $0.99. Read `permission_denials` before a verdict, since each expects its test to run.

- Cache: the report blames the order service; the cause is the shared cache's invalidation. Limit: the check runs one thread, so dropping the cache just before the commit passes.
- Report: 200,000 rows through `reports.render` and `python3 -m reports`, each under 10 seconds of CPU, matching the shipped code's output and errors on awkward input. Limit: the visible `test_report.py` allows 10 seconds of wall-clock time, so a right answer can fail it on a loaded machine.
- Migration: an export with every v2 quirk many times over, under 15 seconds, amounts past 2**53 minor units kept exact, and an amount with too many places refused with no v3 file left. Limit: the docs name every quirk.
- Archive: 24 symlink chains through `safe_extract` and both callers, and 4 normal archives that must unpack as they are, watched by an audit hook. Limits: a child process's writes and undone writes inside the folder aren't seen.

## Work that isn't code

`legal-services-agreement` (class `document`), `finance-ledger-reconcile` and `finance-runway-forecast` (`analysis`), `research-study-synthesis` (`research`) and `ops-inbox-triage` (`operations`) give the agent documents or data and grade an answer file with fixed keys. Each `wrong/` is a mistake a careless reading makes. The finance checks work their answer out from the shipped `repo/` data, never from the agent's copy. `document` and `operations` have one task each, so their verdicts rest on one task.
