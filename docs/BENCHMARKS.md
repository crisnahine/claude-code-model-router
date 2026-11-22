# Benchmarks

Cost and quality of Model Router against Claude Code without it. 12 runs, $0.85 at list price, on 2026-11-22.

This is a pilot. Every row is PARTIAL because each configuration ran each task too few times to tell a difference from noise. Claim nothing from it beyond what the raw data shows.

## Results

| Task class | plugin | defaults | haiku | Rank | Status |
| :- | :- | :- | :- | -: | :- |
| lookup | 1 of 1 solved (100%; 100% to 100%); $0.0421 per solved ($0.0421 to $0.0421) | 1 of 1 solved (100%; 100% to 100%); $0.0654 per solved ($0.0654 to $0.0654) | 1 of 1 solved (100%; 100% to 100%); $0.0355 per solved ($0.0355 to $0.0355) | 2 | PARTIAL |
| bugfix | 4 of 4 solved (100%; 100% to 100%); $0.0944 per solved ($0.0778 to $0.1117) | 4 of 4 solved (100%; 100% to 100%); $0.0719 per solved ($0.0671 to $0.0765) | 1 of 1 solved (100%; 100% to 100%); $0.0383 per solved ($0.0383 to $0.0383) | 3 | PARTIAL |

- lookup: PARTIAL, too few runs (plugin 1, haiku 1; 10 needed).
- bugfix: PARTIAL, too few runs (plugin 4, haiku 1; 10 needed).

## How to read it

- A task counts as solved only when its pass check passes. A wrong answer is a failure, the router's too.
- Cost per solved task is a configuration's total spend on a task class divided by the tasks it solved, so failed runs count against it.
- Intervals are 95% percentile bootstrap intervals over runs (2,000 resamples, seed 0).
- PASS means the router's cost per solved task is below the best baseline's with the whole 95% interval of the difference below zero, and its pass rate is not behind beyond noise. FAIL means it is behind on either. PARTIAL means a tie, a difference inside the noise, or fewer than 10 runs per configuration. The best baseline is the one with the lowest cost per solved task.
- Rank is the router's place among the configurations by cost per solved task, 1 being cheapest.

## Setup

- Claude Code: 2.1.330 (Claude Code). Session model: sonnet, the same for every configuration except one that sets its own (`opusplan`).
- Configurations run: `defaults` (No plugin: subagents inherit the session model at its default effort); `haiku` (No plugin: every subagent on haiku (CLAUDE_CODE_SUBAGENT_MODEL=haiku, CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1)); `plugin` (Model Router loaded with --plugin-dir).
- Models that ran, with Claude Code's list-price cost across all runs: `claude-haiku-5-5` $0.0094 over 5 runs; `claude-sonnet-5-5` $0.8371 over 12 runs. Each run's raw line also carries the registry's prices for those models.
- Agents started, by configuration: `defaults` Explore x1, general-purpose x4; `haiku` Explore x1, general-purpose x1; `plugin` model-router:implementer x4, model-router:scout x1, model-router:verifier x2.
- Shape: a single agent. The session hands each task to one subagent with the Agent tool and relays its answer, so the subagent is what each configuration changes.
- Each run starts in a fresh copy of the task's files, outside any repository, with `--setting-sources project`, `--strict-mcp-config`, `--permission-mode dontAsk`, `--allowedTools` `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash(python3 *)`, `Bash(python *)`, `Bash(tail *)`, `Bash(head *)`, `Bash(echo *)` (every part of a compound command must match, so `tail`, `head` and `echo` let a subagent pipe its test output), `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Every inherited `CLAUDE_CODE_*`, `CLAUDECODE` and `CLAUDE_PLUGIN_*` variable is dropped except the provider switches (`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`, so `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and the effort variables are set only by a configuration. Only the router configuration passes `--plugin-dir`.
- The router keeps learned state across sessions (model health, level raises and its ledger) in its plugin data folder, by default `~/.claude/plugins/data/model-router-inline/`, which every `--plugin-dir` session on the machine shares. The runner gives each run a plugins root of its own inside its scratch copy with `CLAUDE_CODE_PLUGIN_CACHE_DIR` (https://code.claude.com/docs/en/env-vars.md), so every router run starts from an empty data folder, never reads or writes the shared one, and its data goes when the scratch copy does. Each raw line records `plugin_data_own` and, for router runs, hashes of the `state.json` and `learned.json` the run started from in `plugin_state`. 3 router run(s) here borrowed the shared folder instead: the runner copied it before the run and put it back after, which also wiped what other sessions wrote there meanwhile. 6 run(s) here predate this: their lines have no `plugin_state`, and their router runs started from whatever earlier runs and other sessions had left there. They also predate the `tail`, `head` and `echo` rules and the stricter `lookup-retry-limit` check, and their `bench_commit` is the commit the harness ran on top of before it was committed. Newer lines carry `bench_dirty`, true when `bench/` or the plugin had uncommitted changes.
- Sessions are saved, as Claude Code saves them by default, so the router's Stop check can read the transcripts and see a test run, the subagents' included. After each run the runner deletes that run's folder under `~/.claude/projects/` (the one named after its scratch copy) and nothing else, and each raw line records `sessions_saved` and `session_dir_removed`. 6 run(s) here ran with `--no-session-persistence`, which saves no transcript, so the Stop check could never see a test run and asked for a verifier after every edit; their lines have no `sessions_saved`.
- Cost is Claude Code's `total_cost_usd`, which includes subagents and is a client-side estimate at list price, not a bill (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md). Every run is capped with `--max-budget-usd`, which a run can pass by up to the cost of one response plus anything that subagents still running at that moment spend before they stop (https://code.claude.com/docs/en/agent-sdk/agent-loop.md#budget-headroom). The runner starts a run only with room for twice its cap under the total, which is a margin, not a bound.
- The prompt cache is shared across runs, so later runs of a configuration may read what earlier ones wrote. The runner rotates the configuration order on each run, and every raw line records its cache tokens.

## Task set

- `bugfix-median` (bugfix): median in stats.py is wrong for an even number of values. The cause: it returns the upper of the two middle values instead of their mean. Fix it so that odd-length input still returns the middle value.
- `bugfix-pagination` (bugfix): page_slice in pages.py returns the wrong page. The cause: pages are numbered from 1, but the code computes the start as if they were numbered from 0. Fix it, and keep the ValueError for a page or size below 1. test_pages.py shows the expected behaviour.
- `cross-currency` (cross-module): Add a `currency` argument (an ISO code, default "USD") to format_price in pricing/money.py. It must use the symbol from SYMBOLS in pricing/symbols.py and the number of decimals from DECIMALS there (2 for a currency DECIMALS doesn't list), so format_price(1234, "EUR") is "€12.34" and format_price(1234, "JPY") is "¥1234". Then make Invoice.total_line in billing/invoice.py and summary_line in reports/summary.py format in their own currency. Output in USD must not change.
- `cross-retry-setting` (cross-module): fetcher.fetch and uploader.upload each hard-code 3 attempts. Make both use the `retries` setting from config.load(), read each time they are called so a changed $APP_CONFIG file takes effect, and keep 3 as the default. A `retries` of 5 means 5 attempts in both.
- `design-cache-invalidation` (design): Read SPEC.md and design how the catalog servers cache prices. Write DESIGN.json in the repository root with exactly these keys: "strategy" (one of "no_cache", "ttl_only", "event_invalidation", "write_through_from_admin"), "ttl_seconds" (a number, or null for none), "max_staleness_seconds" (the longest a changed price can still be served, a number) and "reasons" (a list of short strings).
- `design-rate-limiter` (design): Read SPEC.md and design the rate limiter it asks for. Write DESIGN.json in the repository root with exactly these keys: "algorithm" (one of "token_bucket", "fixed_window", "sliding_log", "sliding_window_counter"), "store" (one of "process_memory", "redis", "postgres"), "key" (one of "ip", "api_key", "user_id"), "rate_per_second" (a number), "burst" (a number) and "reasons" (a list of short strings).
- `impl-parse-duration` (implementation): Implement parse_duration in durations.py exactly as its docstring specifies, using the standard library only.
- `impl-slugify` (implementation): Implement slugify in text_utils.py exactly as its docstring specifies, using the standard library only.
- `lookup-retry-limit` (lookup): In this repository, which function reads the retry limit from the environment, and in which file is it defined? Answer with the file path and the function name.
- `lookup-todo-count` (lookup): How many `# TODO` comments are there in the .py files under src/, subfolders included? Reply with the number.
- `research-changelog` (research): Using only the files under docs/ in this repository, answer: in which fetchkit version did the default of Client.retry change from 3 to 5, in which version could it first be set through an environment variable, and what is that variable? Write RESEARCH.json in the repository root with the keys "default_changed_in" and "configurable_in" (version numbers such as "1.0"), "variable", and "sources" (the paths of the files that state these facts).
- `research-latency` (research): Which deploy first pushed the p95 latency of /search above 300 ms, and which commit did it ship? Use deploys.csv and metrics/, and compute p95 as the nearest-rank 95th percentile of that deploy's /search samples. Write RESEARCH.json in the repository root with the keys "deploy", "commit" and "p95_ms".
- `rootcause-cart` (root-cause): test_cart.py fails. Find out why and fix the code so every test in it passes. Don't change test_cart.py.
- `rootcause-ranking` (root-cause): test_ranking.py fails. Find out why and fix the code so every test in it passes. Don't change test_ranking.py.
- `security-path-traversal` (security-review): Review this repository for a security vulnerability that lets a caller read files they shouldn't. Reply with the one vulnerable line as path:line (for example `app.py:12`) and one sentence on why.
- `security-sql-injection` (security-review): Review this repository for a security vulnerability that lets a caller read or change data they shouldn't. Reply with the one vulnerable line as path:line (for example `app.py:12`) and one sentence on why.

Each task's files and pass check are under `bench/tasks/<id>/`. Each check fails on the files as shipped and passes on a reference answer (`tests/test_bench.py`).

## Raw data and commands

- [`bench/results/pilot-2026-11-22.jsonl`](../bench/results/pilot-2026-11-22.jsonl)
- [`bench/results/sessions-2026-11-22.jsonl`](../bench/results/sessions-2026-11-22.jsonl)

```bash
python3 bench/run.py --tasks <ids> --configs <ids> --runs <n> --budget 0.5 --max-total <usd> --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

## The full suite

A run of the whole current task set under every configuration in `bench/configs.json`, 10 times each, is 16 tasks x 10 configurations x 10 runs = 1600 runs, about $175.70 at list price. Each class is priced at its own mean per run here (bugfix $0.0782, lookup $0.0477), and a class with no runs yet at the mean of all runs ($0.0705). A configuration whose subagents run a dearer model than Sonnet is priced up by that model's output price over Sonnet's, on the whole run: `plugin` x2, `opus` x2, `fable` x4, `advisor` x2. That scales the price per token only: a dearer model may use more or fewer tokens, the class means pool every configuration's runs, and the new classes are harder than the pilot's two, so treat the figure as a guide, not a bound. At the per-run cap the pilot used, 1600 runs x $0.5 come to $800.00 before overshoot, which has no fixed limit; only `--max-total` stops spend. The full suite needs the maintainer's go-ahead before it runs.

Not covered yet: the fan-out, workflow, team and redo shapes; `haiku`, `opus` and `fable` at `high` effort; a cold and a warm prompt cache measured apart; router overhead per spawn and per turn; and each `priority` setting. `opusplan` in a headless `dontAsk` run never enters plan mode, so it runs Sonnet throughout (https://code.claude.com/docs/en/model-config.md#opusplan-model-setting, https://code.claude.com/docs/en/permission-modes.md). The advisor needs the Anthropic API, Claude Platform on AWS or Claude Models on Google Cloud (https://code.claude.com/docs/en/advisor.md), and the `fable` configuration needs Fable access.
