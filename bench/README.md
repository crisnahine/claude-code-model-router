# Benchmark

Measures what Model Router costs per solved task, and how often tasks get solved, against Claude Code without it. Results go to [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). Standard-library Python only. Nothing here ships with the plugin.

- `tasks/<id>/`: one task each. `task.json` gives its class and brief, `repo/` is the fixed set of files a run starts from, and `check.py <workdir> <answer-file>` exits 0 only when the task was solved. `solution/` (for tasks that change code) or `reference_answer` (for lookups) is a known-good result; `tests/test_bench.py` checks that each pass check fails on `repo/` as shipped and passes on that result.
- `configs.json`: the configurations. `plugin` loads the router with `--plugin-dir`. `defaults` runs Claude Code with no plugin. `haiku`, `sonnet` and `opus` force every subagent onto one model with `CLAUDE_CODE_SUBAGENT_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`.
- `run.py`: runs each task under each configuration with `claude -p --output-format json`, checks the result, and appends one JSON line per run to `results/`.
- `analyze.py`: computes pass rate and cost per solved task with 95% bootstrap intervals and writes `docs/BENCHMARKS.md`.

## Running it

Every run spends real money at list price, or counts against a subscription's usage. `--budget` caps each run with `--max-budget-usd`, which a run can pass by one response plus what its running subagents spend before they stop. `--max-total` starts a run only with room for twice its cap, a margin rather than a bound.

```bash
python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku \
    --runs 1 --budget 0.5 --max-total 3 --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

Leave out `--tasks` or `--configs` to run all of them. `--model` sets the session model, the same for every configuration (default `sonnet`).

Each run gets a fresh copy of the task's files in a temporary directory, never the repository. It loads no user settings, CLAUDE.md, auto memory or MCP servers, and the runner drops any `CLAUDE_CODE_*` variable it inherits except the provider switches (`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`. A run that loads the router writes the plugin's state to `~/.claude/plugins/data/model-router-inline/`, as any `--plugin-dir` session does, so the runner copies that folder to `model-router-inline.bench-saved` next to it before each router run, puts it back afterwards and records hashes of its `state.json` and `learned.json`. If a bench is killed mid-run, that copy holds the real state: copy it back before the next bench, which refuses to start while it exists. Don't run other `--plugin-dir` sessions during a bench: what they write there during a router run is lost.

## Adding a task

Make `tasks/<id>/` with `task.json` (`class` is one of the names in `run.CLASSES`), `repo/`, `check.py`, and `solution/` or `reference_answer`, plus a `wrong_answer` for a lookup. Run `python3 -m unittest tests.test_bench`. A check must be deterministic: the same files and answer always give the same exit code.
