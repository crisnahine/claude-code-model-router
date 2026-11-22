# Benchmark

Measures what Model Router costs per solved task, and how often tasks get solved, against Claude Code without it. Results go to [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). Standard-library Python only. Nothing here ships with the plugin.

- `tasks/<id>/`: one task each. `task.json` gives its class and brief, `repo/` is the fixed set of files a run starts from, and `check.py <workdir> <answer-file>` exits 0 only when the task was solved. `solution/` (for tasks that change files, or write the DESIGN.json, RESEARCH.json or FINDING.json their brief asks for) or `reference_answer` (for an answer given in the reply: lookups) is a known-good result; `tests/test_bench.py` checks that each pass check fails on `repo/` as shipped and passes on that result.
- `configs.json`: the configurations. `plugin` loads the router with `--plugin-dir`. `defaults` runs Claude Code with no plugin. `haiku`, `sonnet`, `opus` and `fable` force every subagent onto one model with `CLAUDE_CODE_SUBAGENT_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`. `sonnet-medium` and `sonnet-high` also set `CLAUDE_CODE_SUBAGENT_EFFORT`. `opusplan` sets the session model (`model`), and `advisor` adds `--advisor opus` (`args`). `price_model` names the model the full-suite estimate prices each configuration's runs at.
- `run.py`: runs each task under each configuration with `claude -p --output-format json`, checks the result, and appends one JSON line per run to `results/`.
- `analyze.py`: computes pass rate and cost per solved task with 95% bootstrap intervals and writes `docs/BENCHMARKS.md`.

## Running it

Every run spends real money at list price, or counts against a subscription's usage. `--budget` caps each run with `--max-budget-usd`, which a run can pass by one response plus what its running subagents spend before they stop. `--max-total` starts a run only with room for twice its cap, a margin rather than a bound.

```bash
python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku \
    --runs 1 --budget 0.5 --max-total 3 --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

Leave out `--tasks` or `--configs` to run all of them. `--model` sets the session model, the same for every configuration that doesn't set its own, as `opusplan` does (default `sonnet`).

Each run gets a fresh copy of the task's files in a temporary directory, never the repository. It loads no user settings, CLAUDE.md, auto memory or MCP servers, and the runner drops any `CLAUDE_CODE_*` variable it inherits except the provider switches (`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`. Each run also gets a plugins root of its own in that temporary directory (`CLAUDE_CODE_PLUGIN_CACHE_DIR`), so the router starts every run from an empty data folder and never reads or writes `~/.claude/plugins/data/model-router-inline/`, which other `--plugin-dir` sessions share. Its data goes with the temporary directory.

Sessions are saved, as Claude Code saves them by default, because the router's Stop check reads the transcripts to see whether a test ran. After each run the runner deletes that run's folder under `~/.claude/projects/`, the one named after its scratch copy, and nothing else.

## Adding a task

Make `tasks/<id>/` with `task.json` (`class` is one of the names in `run.CLASSES`), `repo/`, `check.py`, and `solution/` or `reference_answer`, plus a `wrong_answer` for an answer given in the reply. A task whose answer is one choice among candidates, such as a security review naming the vulnerable line, asks for a JSON file with fixed keys instead of a reply, so the check reads one value and a list, a range or a second finding fails. Run `python3 -m unittest tests.test_bench`. A check must be deterministic: the same files and answer always give the same exit code. A check that imports the run's code imports its own standard-library modules first and counts a `SystemExit` as a failure; it assumes the run isn't trying to fool it, since code that kills the checker process from inside could still exit it with 0.
