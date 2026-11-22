# Benchmarks

Cost and quality of Model Router against Claude Code without it. 480 runs, $44.54 at list price, on 2026-11-22.

Claim "cheapest" or "best" only for a PASS row, and link its raw data.

## Results: session model `claude-opus-5-5`

Every run here had `claude-opus-5-5` as its session model; router: `plugin`.

| Task class | plugin | defaults | Rank | Status |
| :- | :- | :- | -: | :- |
| lookup | 10 of 10 solved (100%; 100% to 100%); $0.0642 per solved ($0.0632 to $0.0653) | 10 of 10 solved (100%; 100% to 100%); $0.0989 per solved ($0.0925 to $0.1074) | 1 | PASS |
| implementation | 10 of 10 solved (100%; 100% to 100%); $0.1221 per solved ($0.1164 to $0.1281) | 10 of 10 solved (100%; 100% to 100%); $0.1771 per solved ($0.1652 to $0.1891) | 1 | PASS |
| bugfix | 10 of 10 solved (100%; 100% to 100%); $0.0991 per solved ($0.0962 to $0.1020) | 10 of 10 solved (100%; 100% to 100%); $0.1277 per solved ($0.1247 to $0.1311) | 1 | PASS |
| cross-module | 10 of 10 solved (100%; 100% to 100%); $0.1566 per solved ($0.1426 to $0.1709) | 10 of 10 solved (100%; 100% to 100%); $0.1895 per solved ($0.1799 to $0.1990) | 1 | PASS |
| security-review | 10 of 10 solved (100%; 100% to 100%); $0.1071 per solved ($0.1052 to $0.1093) | 10 of 10 solved (100%; 100% to 100%); $0.1322 per solved ($0.1283 to $0.1358) | 1 | PASS |
| root-cause | 10 of 10 solved (100%; 100% to 100%); $0.1065 per solved ($0.1029 to $0.1102) | 10 of 10 solved (100%; 100% to 100%); $0.1292 per solved ($0.1248 to $0.1341) | 1 | PASS |
| design | 10 of 10 solved (100%; 100% to 100%); $0.1707 per solved ($0.1482 to $0.1932) | 10 of 10 solved (100%; 100% to 100%); $0.1878 per solved ($0.1744 to $0.2010) | 1 | PARTIAL |
| research | 10 of 10 solved (100%; 100% to 100%); $0.1498 per solved ($0.1208 to $0.2017) | 10 of 10 solved (100%; 100% to 100%); $0.1697 per solved ($0.1600 to $0.1821) | 1 | PARTIAL |

- lookup: PASS, cost per solved task below defaults's; 95% interval of the difference $-0.0431 to $-0.0283.
- implementation: PASS, cost per solved task below defaults's; 95% interval of the difference $-0.0681 to $-0.0419.
- bugfix: PASS, cost per solved task below defaults's; 95% interval of the difference $-0.0329 to $-0.0245.
- cross-module: PASS, cost per solved task below defaults's; 95% interval of the difference $-0.0496 to $-0.0152.
- security-review: PASS, cost per solved task below defaults's; 95% interval of the difference $-0.0292 to $-0.0207.
- root-cause: PASS, cost per solved task below defaults's; 95% interval of the difference $-0.0288 to $-0.0169.
- design: PARTIAL, not significant against defaults.
- research: PARTIAL, not significant against defaults.

### Run metrics

| Task class | plugin | defaults |
| :- | :- | :- |
| lookup | p50 12 s, p90 14 s, cache 83%, accuracy by level: lookup 10/10, escalated 0 of 10 spawns | p50 17 s, p90 21 s, cache 85%, not routed |
| implementation | p50 27 s, p90 34 s, cache 86%, accuracy by level: standard 10/10, escalated 0 of 10 spawns | p50 42 s, p90 47 s, cache 87%, not routed |
| bugfix | p50 18 s, p90 19 s, cache 87%, accuracy by level: standard 10/10, escalated 0 of 10 spawns | p50 26 s, p90 29 s, cache 88%, not routed |
| cross-module | p50 39 s, p90 49 s, cache 87%, accuracy by level: standard 5/5, complex 5/5, escalated 0 of 10 spawns | p50 49 s, p90 61 s, cache 89%, not routed |
| security-review | p50 23 s, p90 25 s, cache 89%, accuracy by level: complex 10/10, escalated 0 of 10 spawns | p50 32 s, p90 36 s, cache 89%, not routed |
| root-cause | p50 21 s, p90 22 s, cache 89%, accuracy by level: standard 10/10, escalated 0 of 10 spawns | p50 26 s, p90 29 s, cache 88%, not routed |
| design | p50 34 s, p90 49 s, cache 85%, accuracy by level: complex 5/5, hardest 5/5, escalated 0 of 10 spawns | p50 44 s, p90 54 s, cache 87%, not routed |
| research | p50 28 s, p90 31 s, cache 86%, accuracy by level: standard 7/7, complex 2/2, frontier 1/1, escalated 0 of 10 spawns | p50 44 s, p90 47 s, cache 88%, not routed |

## Results: session model `sonnet`

Every run here had `sonnet` as its session model; router: `plugin`.

| Task class | plugin | defaults | sonnet-high | sonnet-medium | Rank | Status |
| :- | :- | :- | :- | :- | -: | :- |
| lookup | 10 of 10 solved (100%; 100% to 100%); $0.0366 per solved ($0.0358 to $0.0374) | 10 of 10 solved (100%; 100% to 100%); $0.0479 per solved ($0.0443 to $0.0516) | 10 of 10 solved (100%; 100% to 100%); $0.0494 per solved ($0.0465 to $0.0529) | 9 of 10 solved (90%; 70% to 100%); $0.0542 per solved ($0.0472 to $0.0713) | 1 | PASS |
| implementation | 10 of 10 solved (100%; 100% to 100%); $0.0783 per solved ($0.0727 to $0.0859) | 10 of 10 solved (100%; 100% to 100%); $0.0697 per solved ($0.0659 to $0.0732) | 10 of 10 solved (100%; 100% to 100%); $0.0744 per solved ($0.0714 to $0.0774) | 10 of 10 solved (100%; 100% to 100%); $0.0726 per solved ($0.0672 to $0.0800) | 4 | FAIL |
| bugfix | 10 of 10 solved (100%; 100% to 100%); $0.0638 per solved ($0.0610 to $0.0674) | 10 of 10 solved (100%; 100% to 100%); $0.0547 per solved ($0.0519 to $0.0576) | 10 of 10 solved (100%; 100% to 100%); $0.0575 per solved ($0.0545 to $0.0608) | 10 of 10 solved (100%; 100% to 100%); $0.0580 per solved ($0.0556 to $0.0603) | 4 | FAIL |
| cross-module | 10 of 10 solved (100%; 100% to 100%); $0.0940 per solved ($0.0890 to $0.0996) | 10 of 10 solved (100%; 100% to 100%); $0.0823 per solved ($0.0790 to $0.0857) | 10 of 10 solved (100%; 100% to 100%); $0.0888 per solved ($0.0832 to $0.0943) | 10 of 10 solved (100%; 100% to 100%); $0.0853 per solved ($0.0813 to $0.0906) | 4 | FAIL |
| security-review | 10 of 10 solved (100%; 100% to 100%); $0.0753 per solved ($0.0740 to $0.0766) | 10 of 10 solved (100%; 100% to 100%); $0.0635 per solved ($0.0616 to $0.0654) | 10 of 10 solved (100%; 100% to 100%); $0.0656 per solved ($0.0637 to $0.0675) | 10 of 10 solved (100%; 100% to 100%); $0.0653 per solved ($0.0639 to $0.0668) | 4 | FAIL |
| root-cause | 10 of 10 solved (100%; 100% to 100%); $0.0694 per solved ($0.0661 to $0.0726) | 10 of 10 solved (100%; 100% to 100%); $0.0661 per solved ($0.0629 to $0.0687) | 10 of 10 solved (100%; 100% to 100%); $0.0642 per solved ($0.0617 to $0.0669) | 10 of 10 solved (100%; 100% to 100%); $0.0638 per solved ($0.0610 to $0.0663) | 4 | FAIL |
| design | 9 of 10 solved (90%; 70% to 100%); $0.1195 per solved ($0.0828 to $0.1728) | 8 of 10 solved (80%; 50% to 100%); $0.0835 per solved ($0.0637 to $0.1312) | 9 of 10 solved (90%; 70% to 100%); $0.0802 per solved ($0.0673 to $0.1081) | 10 of 10 solved (100%; 100% to 100%); $0.0703 per solved ($0.0661 to $0.0746) | 4 | FAIL |
| research | 10 of 10 solved (100%; 100% to 100%); $0.1262 per solved ($0.0829 to $0.1887) | 10 of 10 solved (100%; 100% to 100%); $0.0733 per solved ($0.0703 to $0.0763) | 10 of 10 solved (100%; 100% to 100%); $0.0767 per solved ($0.0742 to $0.0793) | 10 of 10 solved (100%; 100% to 100%); $0.0779 per solved ($0.0738 to $0.0834) | 4 | FAIL |

- lookup: PASS, cost per solved task below defaults's; 95% interval of the difference $-0.0151 to $-0.0077.
- implementation: FAIL, cost per solved task above defaults's; 95% interval of the difference $0.0018 to $0.0169.
- bugfix: FAIL, cost per solved task above defaults's; 95% interval of the difference $0.0049 to $0.0134.
- cross-module: FAIL, cost per solved task above defaults's; 95% interval of the difference $0.0054 to $0.0178.
- security-review: FAIL, cost per solved task above defaults's; 95% interval of the difference $0.0096 to $0.0140.
- root-cause: FAIL, cost per solved task above sonnet-medium's; 95% interval of the difference $0.0015 to $0.0099.
- design: FAIL, cost per solved task above sonnet-medium's; 95% interval of the difference $0.0119 to $0.1025.
- research: FAIL, cost per solved task above defaults's; 95% interval of the difference $0.0090 to $0.1151.

### Run metrics

| Task class | plugin | defaults | sonnet-high | sonnet-medium |
| :- | :- | :- | :- | :- |
| lookup | p50 9 s, p90 9 s, cache 87%, accuracy by level: lookup 10/10, escalated 0 of 10 spawns | p50 7 s, p90 9 s, cache 87%, not routed | p50 8 s, p90 10 s, cache 87%, not routed | p50 8 s, p90 10 s, cache 88%, not routed |
| implementation | p50 18 s, p90 21 s, cache 86%, accuracy by level: standard 10/10, escalated 0 of 10 spawns | p50 19 s, p90 21 s, cache 88%, not routed | p50 19 s, p90 22 s, cache 89%, not routed | p50 18 s, p90 21 s, cache 87%, not routed |
| bugfix | p50 13 s, p90 15 s, cache 88%, accuracy by level: standard 9/9, complex 1/1, escalated 0 of 10 spawns | p50 11 s, p90 13 s, cache 88%, not routed | p50 12 s, p90 14 s, cache 88%, not routed | p50 12 s, p90 14 s, cache 88%, not routed |
| cross-module | p50 24 s, p90 26 s, cache 88%, accuracy by level: standard 5/5, complex 5/5, escalated 0 of 10 spawns | p50 22 s, p90 24 s, cache 89%, not routed | p50 22 s, p90 29 s, cache 90%, not routed | p50 22 s, p90 24 s, cache 88%, not routed |
| security-review | p50 19 s, p90 20 s, cache 89%, accuracy by level: complex 10/10, escalated 0 of 10 spawns | p50 16 s, p90 17 s, cache 90%, not routed | p50 17 s, p90 18 s, cache 90%, not routed | p50 16 s, p90 17 s, cache 90%, not routed |
| root-cause | p50 16 s, p90 17 s, cache 89%, accuracy by level: standard 10/10, escalated 0 of 10 spawns | p50 16 s, p90 18 s, cache 90%, not routed | p50 15 s, p90 17 s, cache 90%, not routed | p50 16 s, p90 17 s, cache 90%, not routed |
| design | p50 19 s, p90 39 s, cache 85%, accuracy by level: complex 7/8, hardest 2/2, escalated 0 of 11 spawns | p50 14 s, p90 20 s, cache 87%, not routed | p50 15 s, p90 23 s, cache 87%, not routed | p50 15 s, p90 22 s, cache 88%, not routed |
| research | p50 19 s, p90 28 s, cache 88%, accuracy by level: standard 3/3, complex 5/5, frontier 2/2, escalated 0 of 10 spawns | p50 18 s, p90 19 s, cache 90%, not routed | p50 19 s, p90 21 s, cache 89%, not routed | p50 18 s, p90 20 s, cache 89%, not routed |

## How to read it

- A task counts as solved only when its pass check passes. A wrong answer is a failure, the router's too.
- Cost per solved task is a configuration's total spend on a task class divided by the tasks it solved, so failed runs count against it.
- Intervals are 95% percentile bootstrap intervals over runs (2,000 resamples, seed 0).
- PASS means the router's cost per solved task is below the best baseline's with the whole 95% interval of the difference below zero, and its pass rate is not behind beyond noise. FAIL means it is behind on either. PARTIAL means a tie, a difference inside the noise, or fewer than 10 runs per configuration. The best baseline is the one with the lowest cost per solved task.
- Rank is the router's place among the baselines by cost per solved task, 1 being cheapest. A router variant (another configuration that loads the plugin) has its own column but is never a baseline.
- Run metrics, per class and configuration: p50 and p90 are nearest-rank percentiles of each run's wall time (`wall_ms`, the runner's clock around the `claude` process). Cache is cache-read tokens over all input tokens, which are input plus cache reads plus cache writes (https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md), summed over every model that ran. For a router configuration, accuracy by level is the pass rate of its runs grouped by the level the router gave the run's first subagent. Escalated counts the spawns the router moved one level up because the same call came back for a redo, over the spawns whose ledger row says either way. Both come from the run's `routed` line, and n/a means no run in the cell has it.

## Setup

- Claude Code: 2.1.330 (Claude Code). Session models: claude-opus-5-5, sonnet (`--model`), one results table each. Inside a table every configuration ran on that session model, except one that sets its own (`opusplan`).
- Configurations run: `defaults` (No plugin: subagents inherit the session model at its default effort); `plugin` (Model Router loaded with --plugin-dir); `sonnet-high` (No plugin: every subagent on sonnet at high effort (CLAUDE_CODE_SUBAGENT_EFFORT=high)); `sonnet-medium` (No plugin: every subagent on sonnet at medium effort (CLAUDE_CODE_SUBAGENT_EFFORT=medium)).
- Models that ran, with Claude Code's list-price cost across all runs: `claude-fable-5-2` $0.7783 over 3 runs; `claude-haiku-5-5` $0.0164 over 20 runs; `claude-opus-5-5` $19.1133 over 163 runs; `claude-sonnet-5-5` $24.6360 over 384 runs. Each run's raw line also carries the registry's prices for those models.
- Agents started, by configuration: `defaults` Explore x16, general-purpose x144; `plugin` claude x25, general-purpose x83, model-router:architect x3, model-router:implementer x31, model-router:reviewer x1, model-router:scout x18; `sonnet-high` Explore x9, general-purpose x71; `sonnet-medium` Explore x5, general-purpose x75.
- Router spend by agent type, from the router's ledger (`agent_costs`), over the runs that record it: `plugin` on `claude-opus-5-5` (80 runs): main loop $6.1165 (63%), `claude` $1.2373 (13%), `model-router:implementer` $1.2313 (13%), `general-purpose` $1.1695 (12%), `model-router:scout` $0.0057 (0%); `plugin` on `sonnet` (80 runs): main loop $3.2329 (50%), `general-purpose` $2.6354 (40%), `model-router:implementer` $0.4067 (6%), `model-router:architect` $0.1746 (3%), `model-router:reviewer` $0.0284 (0%), `claude` $0.0277 (0%), `model-router:scout` $0.0054 (0%).
- Each configuration's runs pool every batch it ran in, on the same machine, so a configuration run in a later batch is compared with the baseline runs of earlier ones. Batches, by configuration: `defaults` v011-opus (80), v011-sonnet (80); `plugin` v011-opus (80), v011-sonnet (80); `sonnet-high` v011-sonnet (80); `sonnet-medium` v011-sonnet (80).
- Shape: a single agent. The session hands each task to one subagent with the Agent tool and relays its answer, so the subagent is what each configuration changes.
- Each run starts in a fresh copy of the task's files, outside any repository, with `--setting-sources project`, `--strict-mcp-config`, `--permission-mode dontAsk`, `--allowedTools` `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash(python3 *)`, `Bash(python *)`, `Bash(tail *)`, `Bash(head *)`, `Bash(echo *)` (every part of a compound command must match, so `tail`, `head` and `echo` let a subagent pipe its test output), `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Every inherited `CLAUDE_CODE_*`, `CLAUDECODE` and `CLAUDE_PLUGIN_*` variable is dropped except the provider switches (`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`, so `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and the effort variables are set only by a configuration. Only the router configuration passes `--plugin-dir`.
- The router keeps learned state across sessions (model health, level raises and its ledger) in its plugin data folder, by default `~/.claude/plugins/data/model-router-inline/`, which every `--plugin-dir` session on the machine shares. The runner gives each run a plugins root of its own inside its scratch copy with `CLAUDE_CODE_PLUGIN_CACHE_DIR` (https://code.claude.com/docs/en/env-vars.md), so every router run starts from an empty data folder, never reads or writes the shared one, and its data goes when the scratch copy does. Each raw line records `plugin_data_own` and, for router runs, hashes of the `state.json` and `learned.json` the run started from in `plugin_state`.
- Sessions are saved, as Claude Code saves them by default, so the router's Stop check can read the transcripts and see a test run, the subagents' included. After each run the runner deletes that run's folder under `~/.claude/projects/` (the one named after its scratch copy) and nothing else, and each raw line records `sessions_saved` and `session_dir_removed`.
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
- `security-path-traversal` (security-review): Review this repository for a security vulnerability that lets a caller read files they shouldn't. Write FINDING.json in the repository root with exactly the keys "file" (the vulnerable file's path from the repository root), "line" (the number of the one vulnerable line, an integer) and, if you like, "why" (one sentence).
- `security-sql-injection` (security-review): Review this repository for a security vulnerability that lets a caller read or change data they shouldn't. Write FINDING.json in the repository root with exactly the keys "file" (the vulnerable file's path from the repository root), "line" (the number of the one vulnerable line, an integer) and, if you like, "why" (one sentence).

Each task's files and pass check are under `bench/tasks/<id>/`. Each check fails on the files as shipped and passes on a reference answer (`tests/test_bench.py`).

## Raw data and commands

- [`bench/results/v011-opus-bugfix.jsonl`](../bench/results/v011-opus-bugfix.jsonl)
- [`bench/results/v011-opus-cross-module.jsonl`](../bench/results/v011-opus-cross-module.jsonl)
- [`bench/results/v011-opus-design.jsonl`](../bench/results/v011-opus-design.jsonl)
- [`bench/results/v011-opus-implementation.jsonl`](../bench/results/v011-opus-implementation.jsonl)
- [`bench/results/v011-opus-lookup.jsonl`](../bench/results/v011-opus-lookup.jsonl)
- [`bench/results/v011-opus-research.jsonl`](../bench/results/v011-opus-research.jsonl)
- [`bench/results/v011-opus-root-cause.jsonl`](../bench/results/v011-opus-root-cause.jsonl)
- [`bench/results/v011-opus-security-review.jsonl`](../bench/results/v011-opus-security-review.jsonl)
- [`bench/results/v011-sonnet-bugfix.jsonl`](../bench/results/v011-sonnet-bugfix.jsonl)
- [`bench/results/v011-sonnet-cross-module.jsonl`](../bench/results/v011-sonnet-cross-module.jsonl)
- [`bench/results/v011-sonnet-design.jsonl`](../bench/results/v011-sonnet-design.jsonl)
- [`bench/results/v011-sonnet-implementation.jsonl`](../bench/results/v011-sonnet-implementation.jsonl)
- [`bench/results/v011-sonnet-lookup.jsonl`](../bench/results/v011-sonnet-lookup.jsonl)
- [`bench/results/v011-sonnet-research.jsonl`](../bench/results/v011-sonnet-research.jsonl)
- [`bench/results/v011-sonnet-root-cause.jsonl`](../bench/results/v011-sonnet-root-cause.jsonl)
- [`bench/results/v011-sonnet-security-review.jsonl`](../bench/results/v011-sonnet-security-review.jsonl)

- `bench/results/archive/` holds 12 rows left out of every table: 12 ran on 2 other commits (older builds of the router and the bench), and 12 were recorded while the run's folder name held its task id.
- `bench/results/archive/pre-0.1.1/` holds 798 rows left out of every table, plus 7 set-aside lines under its `infra/` and `limited/`: 798 ran on 5 other commits (older builds of the router and the bench), 52 on an earlier revision of `design-cache-invalidation`, and 798 were recorded while the run's folder name held its task id.

```bash
python3 bench/run.py --tasks <ids> --configs <ids> --runs <n> --budget 0.5 --max-total <usd> --out bench/results/<name>.jsonl
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
```

## The full suite

A run of the whole current task set under every configuration in `bench/configs.json`, 10 times each, is 16 tasks x 19 configurations x 10 runs = 3040 runs, about $430.59 at list price. Each class is priced at its own mean per run here (bugfix $0.0768, cross-module $0.1161, design $0.1126, implementation $0.0990, lookup $0.0576, research $0.1123, root-cause $0.0832, security-review $0.0848), and a class with no runs yet at the mean of all runs ($0.0928). A configuration whose subagents run a dearer model than Sonnet is priced up by that model's output price over Sonnet's, on the whole run: `plugin` x2, `opus` x2, `opus-high` x2, `fable` x4, `fable-high` x4, `advisor` x2. That scales the price per token only: a dearer model may use more or fewer tokens, the class means pool every configuration's runs, and the new classes are harder than the pilot's two, so treat the figure as a guide, not a bound. At the per-run cap the pilot used, 3040 runs x $1 come to $3,040.00 before overshoot, which has no fixed limit; only `--max-total` stops spend. The full suite needs the maintainer's go-ahead before it runs.

Defined in `bench/configs.json` but not run here: `plugin-cheap`, `plugin-cheap-nomain`, `plugin-cheap-nomain-nogate`, `plugin-m6-08-b`, `plugin-m6-08-b-nogate`, `plugin-m6-10`, `haiku`, `haiku-high`, `sonnet`, `opus`, `opus-high`, `fable`, `fable-high`, `opusplan`, `advisor`.

Not covered yet: the fan-out, workflow, team and redo shapes; a cold and a warm prompt cache measured apart; router overhead per spawn and per turn; and each `priority` setting. `opusplan` in a headless `dontAsk` run never enters plan mode, so it runs Sonnet throughout (https://code.claude.com/docs/en/model-config.md#opusplan-model-setting, https://code.claude.com/docs/en/permission-modes.md). The advisor needs the Anthropic API, Claude Platform on AWS or Claude Models on Google Cloud (https://code.claude.com/docs/en/advisor.md), and the `fable` and `fable-high` configurations need Fable access.
