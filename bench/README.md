# Benchmark

Measures what Model Router costs per solved task, and how often tasks get solved, against Claude Code without it. Results go to [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). Standard-library Python only. Nothing here ships with the plugin.

- `tasks/<id>/`: one task each. `task.json` gives its class and brief, `repo/` is the fixed set of files a run starts from, and `check.py <workdir> <answer-file>` exits 0 only when the task was solved. `solution/` (for tasks that change files, or write the DESIGN.json, RESEARCH.json or FINDING.json their brief asks for) or `reference_answer` (for an answer given in the reply: lookups) is a known-good result; `tests/test_bench.py` checks that each pass check fails on `repo/` as shipped and passes on that result.
- `configs.json`: the configurations. `plugin` loads the router with `--plugin-dir`. `defaults` runs Claude Code with no plugin. `haiku`, `sonnet`, `opus` and `fable` force every subagent onto one model with `CLAUDE_CODE_SUBAGENT_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`. `sonnet-medium` and `sonnet-high` also set `CLAUDE_CODE_SUBAGENT_EFFORT`. `opusplan` sets the session model (`model`), and `advisor` adds `--advisor opus` (`args`). `price_model` names the model the full-suite estimate prices each configuration's runs at. A router variant is any other configuration with `"plugin": true`. Its `router_config` becomes the run's own `.claude/model-router.json`, its `plugin_options` go in through `--settings` as `pluginConfigs` (project settings can't set plugin options), and `plugin_ref` loads the plugin exported from that commit instead of the checkout's. `analyze.py` gives a variant its own column but never makes it a baseline.
- `run.py`: runs each task under each configuration with `claude -p --output-format json`, checks the result, and appends one JSON line per run to `results/`.
- `analyze.py`: computes pass rate and cost per solved task with 95% bootstrap intervals and writes `docs/BENCHMARKS.md`.

## Running it

Every run spends real money at list price, or counts against a subscription's usage. `--budget` caps each run with `--max-budget-usd`, which a run can pass by one response plus what its running subagents spend before they stop. `--max-total` starts a run only with room for twice its cap, a margin rather than a bound. A run the API refuses for a rate or usage limit, or one that loses the network (an ENOTFOUND, ENETUNREACH or "Connection lost" error from Claude Code), is not a failed task: the runner pauses (1 minute, then twice as long each time), runs it again, and after 5 pauses stops with exit code 2, keeping every finished run. Limited runs go to `results/limited/`, so their spend is on record but out of the results. `results/archive/` keeps the first pilots, which ran on an older harness (no saved sessions, a plugin data folder shared with other sessions), so they stay out of the comparison.

```bash
python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku \
    --runs 1 --budget 0.5 --max-total 3 --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

Leave out `--tasks` or `--configs` to run all of them. `--model` sets the session model, the same for every configuration that doesn't set its own, as `opusplan` does (default `sonnet`). `analyze.py` writes one results table per session model. `--first-run <n>` numbers the runs from `n`, so a batch run in rounds keeps each round's configuration order. A router run's line carries `agent_costs`, its spend by agent type (`main` is the main loop) from the router's ledger.

A run that failed for the network before the runner caught it goes by hand to `results/infra/`, with the reason in `infra_reason`, and is run again. The page counts those, and the runner's own `results/limited/` lines, as set aside.

Each run gets a fresh copy of the task's files in a temporary directory, never the repository. It loads no user settings, CLAUDE.md, auto memory or MCP servers, and the runner drops any `CLAUDE_CODE_*` variable it inherits except the provider switches (`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`. Each run also gets a plugins root of its own in that temporary directory (`CLAUDE_CODE_PLUGIN_CACHE_DIR`), so the router starts every run from an empty data folder and never reads or writes `~/.claude/plugins/data/model-router-inline/`, which other `--plugin-dir` sessions share. Its data goes with the temporary directory.

Sessions are saved, as Claude Code saves them by default, because the router's Stop check reads the transcripts to see whether a test ran. After each run the runner deletes that run's folder under `~/.claude/projects/`, the one named after its scratch copy, and nothing else.

Each raw line records `bench_commit`, the HEAD it ran on, and `bench_dirty`, whether `bench/` or the plugin had changes HEAD doesn't hold. When the `m6-*` files were recorded, the check also counted the bench's own output under `bench/results/`, so once the first results file of a session was written, every later line said `bench_dirty: true`. That is why the 320 main rows of the `m6-*` files carry it while the 16 probe rows before them don't. The code that ran for all of them is `499d046`; the check now leaves `bench/results/` out.

## Adding a task

Make `tasks/<id>/` with `task.json` (`class` is one of the names in `run.CLASSES`), `repo/`, `check.py`, and `solution/` or `reference_answer`, plus a `wrong_answer` for an answer given in the reply. A task whose answer is one choice among candidates, such as a security review naming the vulnerable line, asks for a JSON file with fixed keys instead of a reply, so the check reads one value and a list, a range or a second finding fails. Run `python3 -m unittest tests.test_bench`. A check must be deterministic: the same files and answer always give the same exit code. A check that imports the run's code imports its own standard-library modules first and counts a `SystemExit` as a failure; it assumes the run isn't trying to fool it, since code that kills the checker process from inside could still exit it with 0.
