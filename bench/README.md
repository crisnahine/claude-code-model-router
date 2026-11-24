# Benchmark

Measures what Model Router costs per solved task, and how often tasks get solved, against Claude Code without it. Results go to [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). Standard-library Python only. Nothing here ships with the plugin.

- `tasks/<id>/`: one task each. `task.json` gives its class, brief and `revision`. Raise the revision, with a line in `revisions`, whenever a change could change a run's outcome; every row records it, and rows on an earlier one are left out of the tables. A change to a check keeps the revision only when it can't move any recorded verdict (where the work dir is gone, the commit body shows why no verdict can move). `repo/` is the fixed set of files a run starts from. `check.py <workdir> <answer-file>` exits 0 when the task was solved, 1 when it wasn't and 2 when the check itself broke: `run.grade` starts every check through a launcher that turns an error the check raises into 2, so a bug in a check can't read as a wrong answer. It returns the verdict, a reason (the last line printed) and a hash of the check's code. Checks share `tasks/passcheck.py`; `run.py` runs them with `python -I`, so neither the check's folder nor the work dir is on `sys.path`. `solution/` (for tasks that change files, or write the DESIGN.json, RESEARCH.json or FINDING.json the brief asks for) or `reference_answer` (for a lookup answered in the reply) is a known-good result, and `tests/test_bench.py` checks that each check fails on `repo/` as shipped and passes on it. `wrong/<name>/` and `right/<name>/` hold files laid over `repo/` and `solution/` (`answer.txt` is the reply and isn't copied, `remove.txt` lists paths to delete); a test asserts that every `wrong/` fails and every `right/` passes, as a verdict and never as broken. A `wrong/` folder can hold an `expect.txt`, a substring the check's reason must contain; one without it must fail as "the check exited 1".
- `configs.json`: the configurations. `plugin` loads the router with `--plugin-dir`. `defaults` runs Claude Code with no plugin. `haiku`, `sonnet`, `opus` and `fable` force every subagent onto one model with `CLAUDE_CODE_SUBAGENT_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`, which needs Claude Code v2.1.257 or later. Forcing makes Claude Code ignore the `model` field in subagent definitions, but a fork, and a skill that runs in a subagent with `model: inherit`, still run on the parent's model (https://code.claude.com/docs/en/sub-agents#run-every-subagent-on-one-model). The bench's prompt starts subagents with the Agent tool, and the recorded runs started only general-purpose, Explore, `claude` and the router's own agents ([docs/BENCHMARKS.md](../docs/BENCHMARKS.md), "Agents started"), so forcing covers them; check a pilot row's `subagents.by_type` for any other type. `sonnet-medium`, `haiku-high`, `sonnet-high`, `opus-high` and `fable-high` also set `CLAUDE_CODE_SUBAGENT_EFFORT`. `opusplan` sets `model`, and `advisor` adds `--advisor opus` (`args`). `price_model` names the model the full-suite estimate prices a configuration at. A router variant is any other configuration with `"plugin": true`: its `router_config` becomes the run's `.claude/model-router.json`, its `plugin_options` go in through `--settings` as `pluginConfigs` (project settings can't set plugin options), and `plugin_ref` loads the plugin exported from that commit instead of the checkout's. `analyze.py` gives a variant its own column but never makes it a baseline.
- `run.py`: runs each task under each configuration with `claude -p --output-format json`, checks the result, and appends one JSON line per run to `results/`.
- `analyze.py`: computes pass rate (exact 95% binomial interval), cost per solved task (95% bootstrap interval) and a quality verdict per class, and writes `docs/BENCHMARKS.md` below the page head, which `tools/docs_assets.py` writes. It gives docs_assets the numbers for the chart and the docs' figures (`figures`, keyed by role: the router, Claude Code's defaults, the best baseline and the strongest forced one; the router is named by role alone, a baseline by role or by its id, as in `BENCH_SERIES`, `PROSE` and span keys such as `opus.pooled.haiku-high`), and the yes/no facts the docs' prose states about each session (`PROSE`, `FACT_TEXT`); `--check` fails when one stops being true.

## Running it

Every run spends real money or subscription usage. `--budget` caps each run with `--max-budget-usd`, which a run can pass by one response plus what its running subagents spend before they stop. `--max-total` starts a run, and each retry of one, only with room for twice its cap (a margin, not a bound); with no room left the runner stops with exit code 1.

A run the API refuses for a rate or usage limit, or one that loses the network (an ENOTFOUND, ENETUNREACH or "Connection lost" error from Claude Code), is not a failed task. The runner pauses (1 minute, then twice as long each time), runs it again, and after 5 pauses stops with exit code 2, keeping every finished run. Limited runs go to `results/limited/`, so their spend is on record but out of the results. Any other error before a cent is spent (an invalid API key, a credit balance too low, a model the account can't use) goes there too, with `limited_reason` `api_error`, without a pause or a retry; 3 in a row stop the runner with exit code 2. A run that failed for the network before the runner caught it goes by hand to `results/infra/`, with `infra_reason`, and is run again. `results/archive/` keeps pilots from an older harness, out of the comparison.

```bash
python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku \
    --runs 1 --budget 0.5 --max-total 3 --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check
```

`--check` builds the page in memory and fails when the file differs. Leave out `--tasks` or `--configs` to run all of them; a list that matches no id stops the runner with exit code 2, naming the ids. Other `run.py` flags: `--model` sets the session model for every configuration that doesn't set its own, as `opusplan` does (default `sonnet`; `analyze.py` writes one table per session model); `--first-run <n>` numbers the runs from `n`, so a batch run in rounds keeps each round's configuration order; `--timeout` kills a run after that many seconds (default 900); `--keep <dir>` copies each run's scratch folder and transcripts there; `--claude` takes an argv prefix (`--claude python3 stub.py`) for a stub or wrapper.

Each run gets a fresh copy of the task's files in a temporary directory, never the repository, and loads no user settings, CLAUDE.md, auto memory or MCP servers. The runner drops any `CLAUDE_CODE_*`, `CLAUDECODE`, `CLAUDE_PLUGIN_*` or `CLAUDE_PROJECT_DIR` variable it inherits, except `CLAUDE_CODE_USE_*` and `CLAUDE_CODE_OAUTH_TOKEN`, and gives each run its own plugins root (`CLAUDE_CODE_PLUGIN_CACHE_DIR`), so the router starts from an empty data folder and never touches `~/.claude/plugins/data/model-router-inline/`. Sessions are saved, because the router's Stop check reads the transcripts to see whether a test ran; afterwards the runner deletes that run's folder under `~/.claude/projects/` and nothing else.

A row records `bench_commit` (HEAD) and `bench_dirty` (uncommitted changes in `bench/` or the plugin). A router row carries `agent_costs`, its spend by agent type (`main` is the main loop), and `routed`, its spawn decisions in order: agent, level, model, effort and, where logged, `escalated_from`. Every row carries `wall_ms`, the runner's clock around `claude`, and the page takes latency from it, never from Claude Code's `duration_ms`. A result of `error_during_execution` with a zero cost counts the whole budget, unless the API refused the run. `grade` holds the `verdict` (`pass`, `fail` or `broken`), the check's `reason` and its `check_hash`; `passed` is true only for `pass`. Analyze leaves a `broken` run out of the tables and counts it in a page note. `final_answer` keeps what the deleted work dir held, so a fixed check can be tried on recorded runs: the reply, each text file the run added or changed, the files it removed, and the names of files left out (caps: 20,000 characters a file, 80,000 a row). A row's `schema` is `run.ROW_SCHEMA`, 2 now; `analyze.py` reads a missing key (`wall_ms`, `routed`, `plugin_state`) as an older harness only on a row below it.

The quality verdict is separate from the cost verdict. Per class, the router's pass rate is tested against every baseline with 10 runs, with an exact one-sided test, Holm-adjusted: `BEHIND` when one solved more beyond noise, `NOT SHOWN BEHIND` when none did, which is not parity, since 10 runs per cell can't show a tie. A class is saturated when the weakest forced baseline can't be told from the strongest by the same test, and then no quality claim is made.

## The quality-tier pilot

Six quality-tier tasks (one for each of six classes) run under `defaults`, `plugin` and the three forced baselines, 3 times each (30 cells, 90 runs):

```bash
python3 bench/run.py --tasks impl-merge-customers bugfix-statement-queries security-admin-route \
    rootcause-wallet-race cross-amount-cents design-checkout-retries \
    --configs defaults plugin haiku sonnet opus --runs 3 --budget 1.5 --max-total 45 \
    --model claude-opus-5-5 --out "bench/results/pilot/quality-pilot-$(date +%F).jsonl"
python3 bench/analyze.py bench/results/pilot/*.jsonl --out "$TMPDIR/quality-pilot.md"
```

The same pilot can run as three processes at once, each with its own two tasks, its own `--out` and a $15 share of the $45 cap:

```bash
pilot() {  # <part> <$ share> <task> <task>
    python3 bench/run.py --tasks "$3" "$4" --configs defaults plugin haiku sonnet opus --runs 3 --budget 1.5 \
        --max-total "$2" --model claude-opus-5-5 --out "bench/results/pilot/quality-pilot-$(date +%F)-$1.jsonl"
}
pilot a 15 impl-merge-customers bugfix-statement-queries &
pilot b 15 security-admin-route rootcause-wallet-race &
pilot c 15 cross-amount-cents design-checkout-retries &
wait
python3 bench/analyze.py bench/results/pilot/*.jsonl --out "$TMPDIR/quality-pilot.md"
```

Each process checks only its own spend, so the shares must add up to $45 or less, and the task sets must not overlap or a cell gets more than 3 runs. Limited rows go to `bench/results/pilot/limited/`. `plugin` is the checkout's router, so run it on the branch you want measured. Rows go to `results/pilot/`, so the page's tables, built from `results/*.jsonl`, don't pool them with the 10-run ones; `analyze.py` picks `plugin` as the router there because it is the only plugin configuration. It lists a task as splitting the models (`splitting_tasks`) when `opus` solved at least 2 of every 3 runs and `sonnet` at most 1 of every 3; that is a reading rule for 3 runs, not a significance test. The 10-run measurement needs the maintainer's go after the pilot.

Cost guide: assuming these tasks cost 3 times the old ones (a guess), the pilot costs about $35. A process stops before its last runs when the real multiplier is above about 3.7 (single) or 3.2 (each $15 share); rows already written are kept.

## Adding a task

Make `tasks/<id>/` with `task.json` (`class` is one of the names in `run.CLASSES`), `repo/`, `check.py`, and `solution/` or `reference_answer`, plus a `wrong_answer` for an answer given in the reply. Give a task `wrong/` answers for each mistake it is built around, and `right/` answers for each fair form of a right one (a test fails for a task with neither a `wrong_answer` nor a `wrong/` folder). A task whose answer is one choice among candidates (a security review naming the vulnerable line) asks for a JSON file with fixed keys, so a list, a range or a second finding fails. Run `python3 -m unittest tests.test_bench`.

A check must be deterministic. It ends with `passcheck.finish(ok, reason)`, which prints the reason a row records (18 older checks end with `sys.exit` instead, and 16 of those give no reason, so their row says "the check exited 1"). Lines that run the work dir's code sit in `with passcheck.agent_code():`, so whatever that code raises (an exit included) fails the task, while an error in the check itself exits 2. A check never catches `Exception` or `BaseException`. To import the run's code, a check adds the work dir with `passcheck.use_work`, which appends it to `sys.path` (so it can't replace a standard-library module). A `SystemExit` the run's code raises fails the task only inside `passcheck.agent_code()`. A check runs the work dir's own tests with `passcheck.tests_pass`, which returns False for a test the run removed or made unreadable, a test that hangs and output that isn't UTF-8 (a shipped test that can't be read raises, so it reads as a broken check). A check that imports the run's code shares its process, so that code can end it with exit code 0.

A check whose task runs the agent's code to decide the answer does not import it. It runs that code in a child, a `probe.py` beside `check.py`, through `passcheck.probe(work, script, payload)`, on data the check generates from a fixed seed, and decides in the parent from the child's last JSON line, since only JSON crosses and the parent compares it exactly. A probe never holds the expected answer.

`run.py` starts every check without credential variables: any whose name holds `TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `AUTH` or `CREDENTIAL`, has `PAT` as a whole word (`GH_PAT`, not `PATH`), or starts with `ANTHROPIC_` or `CLAUDE_CODE_OAUTH`. A credential kept in a file (`~/.aws`, `~/.netrc`) is not hidden from it.
