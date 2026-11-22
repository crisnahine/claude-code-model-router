# Benchmarks

Cost and quality of Model Router against Claude Code without it. 6 runs, $0.38 at list price, on 2026-11-22.

This is a pilot. Every row is PARTIAL because each configuration ran each task too few times to tell a difference from noise. Claim nothing from it beyond what the raw data shows.

## Results

| Task class | plugin | defaults | haiku | Rank | Status |
| :- | :- | :- | :- | -: | :- |
| lookup | 1 of 1 solved (100%; 100% to 100%); $0.0421 per solved ($0.0421 to $0.0421) | 1 of 1 solved (100%; 100% to 100%); $0.0654 per solved ($0.0654 to $0.0654) | 1 of 1 solved (100%; 100% to 100%); $0.0355 per solved ($0.0355 to $0.0355) | 2 | PARTIAL |
| bugfix | 1 of 1 solved (100%; 100% to 100%); $0.1201 per solved ($0.1201 to $0.1201) | 1 of 1 solved (100%; 100% to 100%); $0.0766 per solved ($0.0766 to $0.0766) | 1 of 1 solved (100%; 100% to 100%); $0.0383 per solved ($0.0383 to $0.0383) | 3 | PARTIAL |

- lookup: PARTIAL, too few runs (plugin 1, haiku 1; 10 needed).
- bugfix: PARTIAL, too few runs (plugin 1, haiku 1; 10 needed).

## How to read it

- A task counts as solved only when its pass check passes. A wrong answer is a failure, the router's too.
- Cost per solved task is a configuration's total spend on a task class divided by the tasks it solved, so failed runs count against it.
- Intervals are 95% percentile bootstrap intervals over runs (2,000 resamples, seed 0).
- PASS means the router's cost per solved task is below the best baseline's with the whole 95% interval of the difference below zero, and its pass rate is not behind beyond noise. FAIL means it is behind on either. PARTIAL means a tie, a difference inside the noise, or fewer than 10 runs per configuration. The best baseline is the one with the lowest cost per solved task.
- Rank is the router's place among the configurations by cost per solved task, 1 being cheapest.

## Setup

- Claude Code: 2.1.330 (Claude Code). Session model: sonnet, the same for every configuration.
- Configurations run: `defaults` (No plugin: subagents inherit the session model at its default effort); `haiku` (No plugin: every subagent on haiku (CLAUDE_CODE_SUBAGENT_MODEL=haiku, CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1)); `plugin` (Model Router loaded with --plugin-dir).
- Models that ran, with Claude Code's list-price cost across all runs: `claude-haiku-5-5` $0.0074 over 4 runs; `claude-sonnet-5-5` $0.3707 over 6 runs. Each run's raw line also carries the registry's prices for those models.
- Agents started, by configuration: `defaults` Explore x1, general-purpose x1; `haiku` Explore x1, general-purpose x1; `plugin` model-router:implementer x1, model-router:scout x1, model-router:verifier x1.
- Shape: a single agent. The session hands each task to one subagent with the Agent tool and relays its answer, so the subagent is what each configuration changes.
- Each run starts in a fresh copy of the task's files, outside any repository, with `--setting-sources project`, `--strict-mcp-config`, `--no-session-persistence`, `--permission-mode dontAsk`, `--allowedTools` `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash(python3 *)`, `Bash(python *)`, `Bash(tail *)`, `Bash(head *)`, `Bash(echo *)` (every part of a compound command must match, so `tail`, `head` and `echo` let a subagent pipe its test output), `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Every inherited `CLAUDE_CODE_*`, `CLAUDECODE` and `CLAUDE_PLUGIN_*` variable is dropped except the provider switches (`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`, so `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and the effort variables are set only by a configuration. Only the router configuration passes `--plugin-dir`.
- The router keeps learned state across sessions (model health, level raises and its ledger) in `~/.claude/plugins/data/model-router-inline/`, which every `--plugin-dir` session on the machine shares. The runner copies that folder before each router run and puts it back afterwards, so every router run starts from the state the folder held when the bench began, and each raw line records hashes of its `state.json` and `learned.json` in `plugin_state`. Anything another session writes there during a router run is lost when the folder is put back. 6 run(s) here predate this: their lines have no `plugin_state`, and their router runs started from whatever earlier runs and other sessions had left there. They also predate the `tail`, `head` and `echo` rules and the stricter `lookup-retry-limit` check, and their `bench_commit` is the commit the harness ran on top of before it was committed. Newer lines carry `bench_dirty`, true when `bench/` or the plugin had uncommitted changes.
- Cost is Claude Code's `total_cost_usd`, which includes subagents and is a client-side estimate at list price, not a bill (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md). Every run is capped with `--max-budget-usd`, which a run can pass by up to the cost of one response plus anything that subagents still running at that moment spend before they stop (https://code.claude.com/docs/en/agent-sdk/agent-loop.md#budget-headroom). The runner starts a run only with room for twice its cap under the total, which is a margin, not a bound.
- The prompt cache is shared across runs, so later runs of a configuration may read what earlier ones wrote. The runner rotates the configuration order on each run, and every raw line records its cache tokens.

## Task set

- `bugfix-median` (bugfix): median in stats.py is wrong for an even number of values. The cause: it returns the upper of the two middle values instead of their mean. Fix it so that odd-length input still returns the middle value.
- `bugfix-pagination` (bugfix): page_slice in pages.py returns the wrong page. The cause: pages are numbered from 1, but the code computes the start as if they were numbered from 0. Fix it, and keep the ValueError for a page or size below 1. test_pages.py shows the expected behaviour.
- `impl-parse-duration` (implementation): Implement parse_duration in durations.py exactly as its docstring specifies, using the standard library only.
- `impl-slugify` (implementation): Implement slugify in text_utils.py exactly as its docstring specifies, using the standard library only.
- `lookup-retry-limit` (lookup): In this repository, which function reads the retry limit from the environment, and in which file is it defined? Answer with the file path and the function name.
- `lookup-todo-count` (lookup): How many `# TODO` comments are there in the .py files under src/, subfolders included? Reply with the number.

Each task's files and pass check are under `bench/tasks/<id>/`. Each check fails on the files as shipped and passes on a reference answer (`tests/test_bench.py`).

## Raw data and commands

- [`bench/results/pilot-2026-11-22.jsonl`](../bench/results/pilot-2026-11-22.jsonl)

```bash
python3 bench/run.py --tasks <ids> --configs <ids> --runs <n> --budget 0.5 --max-total <usd> --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

## The full suite

A run of the whole current task set under every configuration in `bench/configs.json`, 10 times each, is 6 tasks x 5 configurations x 10 runs = 300 runs. At this data's mean of $0.0630 per run that is about $18.91. Opus runs cost more than the mean here, so treat it as a floor. The full suite needs the maintainer's go-ahead before it runs.

Not covered yet: the cross-module, security-review, root-cause, design and research task classes; the fan-out, workflow, team and redo shapes; `fable`, `high`-effort, `opusplan` and advisor baselines; and each `priority` setting.
