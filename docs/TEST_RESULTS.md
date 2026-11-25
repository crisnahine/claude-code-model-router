<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="BENCHMARKS.md">Benchmarks</a> &nbsp;&middot;&nbsp; Next: <a href="TRACEABILITY.md">Traceability</a></p>
<!-- End of page head. -->

# Test results

The latest results, with the commands that produce them. Replace this file's numbers whenever a gate is rerun; never edit a number you didn't see.

## CI on `main`

The last CI run whose job logs were read for this table is [45361583123](https://github.com/crisnahine/claude-code-model-router/actions/runs/45361583123), on the 0.1.2 release merge `9e247d3`, started 2026-11-24 09:03 UTC: success, the 12 jobs that ran (`eval` doesn't run on push), 710 tests on each Python job. The release PR's first run, [45357531102](https://github.com/crisnahine/claude-code-model-router/actions/runs/45357531102) on `d51403e`, failed on Windows; `fc6fb34` fixed it, and PR run [45360301092](https://github.com/crisnahine/claude-code-model-router/actions/runs/45360301092) passed. That run tested 0.1.2. The redesigned tree at `0c700b9`, where Claude names each call's model and effort, is on no remote branch and has not run in CI.

| Job | Result | Tests | Took |
| :- | :- | :- | -: |
| `test (ubuntu-latest, 3.13)` | success | 710 OK, 1 skipped | 265 s |
| `test (ubuntu-24.04, 3.9)` | success | 710 OK, 1 skipped | 275 s |
| `test (macos-latest, 3.9)` | success | 710 OK, 1 skipped | 409 s |
| `test (macos-latest, 3.13)` | success | 710 OK, 1 skipped | 353 s |
| `test (windows-latest, 3.9)` | success | 710 OK, 10 skipped | 409 s |
| `test (windows-latest, 3.13)` | success | 710 OK, 10 skipped | 330 s |
| `mod (ubuntu-latest)` | success | 30 of 30 pass | 6 s |
| `mod (macos-latest)` | success | 30 of 30 pass | 10 s |
| `mod (windows-latest)` | success | 30 of 30 pass | 18 s |
| `typecheck` | success | the mod type-checks with `tsc` | 11 s |
| `validate (2.1.329)` | success | marketplace and plugin both "Validation passed" | 19 s |
| `validate (2.1.330)` | success | marketplace and plugin both "Validation passed" | 12 s |

Counts come from each job's log (`gh run view --job <id> --log`), times from its start and end. Later runs on `main` (`gh run list --branch main`) aren't tabulated here. CI runs on pushes to `main` and on pull requests.

## Local

Run 2026-11-25 on macOS (Darwin 27.2.0), on the tree committed as `6b93b5f`, the 0.2.0 commit on top of the five merges of the `v03-*` fix branches. The commit that records these results changes only docs.

| Gate | Command | Result |
| :- | :- | :- |
| Unit tests, Python 3.9.6 | `python3 -m unittest discover -s tests` (`python3` is the system `/usr/bin/python3` here) | 735 tests, OK (skipped=1), 388 s |
| Unit tests, Python 3.13.13 | the same with `python3.13` | 735 tests, OK (skipped=1), 288 s |
| Mod tests, Node 22.22.3 | `node --experimental-strip-types --test "tests/mod/*.test.ts"` | 35 of 35 pass |
| Marketplace, Claude Code 2.1.331 | `claude plugin validate --strict .` | Validation passed |
| Plugin, Claude Code 2.1.331 | `claude plugin validate --strict plugins/model-router` | Validation passed |
| Mod and its test, `tsc` 6.0.3 | `sh tests/mod/typecheck.sh` (published types at a pinned commit, `@types/node` 22.20.5) | No errors |
| Docs assets | `python3 tools/docs_assets.py --check` | Every generated file is current, exit 0 |
| Benchmarks page | `python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check` | The committed page matches the results |
| Traceability count | `python3 tools/traceability.py` | Matches the summary line in [TRACEABILITY.md](TRACEABILITY.md) |
| Plugin evals, Claude Code 2.1.331 | `claude plugin eval plugins/model-router --trust-plugin --ablation with-without --runs 3 --max-cost-usd 2 --no-publish` (below) | The five cases ran on this tree on 2026-11-24 at 22:30 UTC on Claude Code 2.1.331, three runs a case with and without the plugin, for $0.65, before this release's redo fix: 2 of 5 cases passed with the plugin and none without, overall score 0.79 against the 0.8 threshold, mean delta 0.61. With the plugin Claude named Haiku for the lookup in 3 of 3 runs and picked the architect on Opus for the design task that names no agent in 3 of 3. The review case passed 2 of 3: in one run the Haiku session answered without delegating. The contract case passed 0 of 3: Claude named Opus in 2 of 3 runs, and the grader that looks for the answer calling the $10 liability cap unusual failed in all three. The redo case marked its redos in 3 of 3 runs but stayed on Haiku, because each redo named haiku again and a redo ran as named. After the fix (a redo that names the same or less climbs anyway) the redo case alone ran again at 22:48 UTC for $0.21: the second redo reached Sonnet in 3 of 3 runs with the plugin and in 0 of 3 without, and 2 of 3 runs passed every grader (in the third the redo's description was worded differently from the grader's text). The full suite ran twice more on the final tree on 2026-11-25. At 00:14 UTC, for $0.76, it scored 0.79 again: the redo reached Sonnet in 3 of 3 runs, but two graders failed on wording alone (one wanted the redo's description word for word, the other a fixed phrase about the $10 cap). With those two graders corrected (a description that starts with "Redo:", and a wider set of words for the cap), the run at 00:25 UTC, for $0.64, scored 0.82 against the 0.8 threshold and passed 3 of 5 cases: the lookup, the design task that names no agent and the redo each passed 3 of 3. The contract and the review each passed 1 of 3: from a Haiku session Claude named Opus for them in 1 of 3 runs, and otherwise named no model, so the call ran on Haiku. Two changes followed that run. The session-start note now says "Here every Agent call names both" where it said "Calls name both": in a side run of the contract and review cases from a Haiku session, Opus was named in 6 of 6 runs with the new sentence, against 2 of 6 with the old one in the run before, and from a Sonnet session the old sentence already got 6 of 6 ($0.65 for both side runs). With every call naming both, a redo began to name Haiku at `xhigh` after Haiku at `high`, and it ran as named, so the redo case fell to 1 of 3 in the run at 01:04 UTC ($0.66, score 0.90). Effort past a model's `effort_stops_at` now counts as no more, so that redo moves to Sonnet. The run at 01:13 UTC, for $0.67, scored 0.89 and passed 4 of 5 cases: the lookup, the design task and the review 3 of 3 each, the redo 2 of 3, the contract 1 of 3. Traced reruns of the two weaker cases right after ($0.28) passed 4 of 4 each, so the contract case swings between runs: over its 13 runs on the new sentence, Opus was named in 11. The last full run passes this gate |

The 735 unit tests are 402 in `tests/test_model_router.py`, 203 in `tests/test_bench.py`, 32 in `tests/test_docs_assets.py`, 18 in `tests/test_trust_and_floors.py`, 13 in `tests/test_stop_check_commands.py`, 13 in `tests/test_upstream_check.py`, 11 in `tests/test_project_trust.py`, 10 in `tests/test_cli_arguments.py`, 10 in `tests/test_mod_and_health.py`, 10 in `tests/test_ci.py`, 5 in `tests/test_store_lock.py`, 3 in `tests/test_traceability.py`, 3 in `tests/test_skill_and_agent_lookup.py` and 2 in `tests/test_session_model_and_teammates.py`, counted with the unittest loader. The one skip is `ModApiTypes.test_router_ts_uses_only_events_and_fields_the_types_declare`: it needs the installed Claude Code's mod types, and none were found here (set `MODEL_ROUTER_MOD_TYPES` to run it).

Not run here: live sessions (the last ones are in `docs/ARCHITECTURE.md` "Verified live", all on the design before this one), the plugin evals and the benchmark.

## Plugin evals

### Routing cases

The cases changed after this run: the session note now has Claude name a model and effort for each kind of work, the redo case marks each redo "Redo:", and a contract summary and a design question that names no agent were added. The five cases haven't run live yet, so the numbers below are for the earlier three, run on the design before this one, which picked the model from the brief.

Run 2026-11-24 on Claude Code 2.1.331, on the working tree at `338dc7e` plus the change that recorded it, on the three cases in `plugins/model-router/evals/`:

```bash
CLAUDE_CODE_PLUGIN_CACHE_DIR=<scratch>/plugins claude plugin eval plugins/model-router --trust-plugin \
  --ablation with-without --runs 3 -j 2 --max-cost-usd 2 --no-publish --output-dir <scratch>/out --json <scratch>/result.json
```

It ran from the checkout, which registered no linked worktrees at the time (earlier runs used a `git archive` export, since the CLI refuses a plugin folder whose git directory registers more linked worktrees than it can screen), in the same clean environment as the first run below. The lookup and review cases told Claude to make one `Agent` call with an exact brief and no model; the redo case told it to make that call, then exactly the same call again. A `regex` grader reads the run's trace for the `resolvedModel` Claude Code reports on each call's result. Without the router a subagent with no model runs on the session's model, so the no-plugin arm fails by design. No `--allow-tools` grant is needed: routing runs in the plugin's hooks and mod, which start their own processes, not through the Bash tool.

| Case | Session | Grader | With the plugin | Without | Cost with / without (3 runs each) |
| :- | :- | :- | -: | -: | -: |
| `route-lookup-subagent` | Sonnet | `ran-on-haiku` | 1.0, 3 of 3 | 0, 0 of 3 | $0.102 / $0.126 |
| `route-review-subagent` | Haiku | `ran-on-opus` | 1.0, 3 of 3 | 0, 0 of 3 | $0.095 / $0.008 |
| `route-redo-escalates` | Haiku | `called-twice`, `first-ran-on-haiku`, `redo-ran-on-sonnet` | 1.0, 3 of 3 | 0.67, 0 of 3 | $0.048 / $0.013 |

Total $0.392 (no LLM grader, so no judge cost), 99 seconds, not partial. Every with-plugin run passed every grader and no without-plugin run passed, so the mean delta is 0.78 (the redo case's is 0.33, since without the plugin the call is still made twice). The trace carries each subagent's model but not its effort, and a grader can't read the spend log in the plugin's data folder (https://code.claude.com/docs/en/plugin-evals.md#what-a-grader-can-look-at), so effort and spend rows are covered by the unit tests and the live runs in `docs/ARCHITECTURE.md`.

### The first run, on the cases these replaced

Run 2026-11-22 on Claude Code 2.1.330 against `atd-7385-next`, with the command above and `--max-cost-usd 3`. It ran in a clean environment (no `CLAUDECODE`, `CLAUDE_CODE_*` or `CLAUDE_CODE_SUBAGENT_MODEL`), with a scratch plugins root and the report kept local (`--no-publish`; by default the CLI publishes it to claude.ai). No `--model` was set, so each case ran on the account's default model, and the LLM graders used the default judge, Haiku, with three votes each. The `with-without` arm runs each case with and without the plugin (https://code.claude.com/docs/en/plugin-evals.md).

| Case | Graders | With the plugin | Without | Cost with / without (3 runs each) |
| :- | :- | -: | -: | -: |
| `route-hard-task` | `picks-strong-tier` (LLM), `skill-fired` (with-only: the `route` skill was invoked) | 1.0, 3 of 3 | 1.0, 3 of 3 | $0.28 / $0.23 |
| `route-trivial-task` | `picks-cheap-tier` (LLM), `no-fable` (regex) | 1.0, 3 of 3 | 1.0, 3 of 3 | $0.22 / $0.88 |

Total $1.61 including the judge, 148 seconds, not partial. Every judge vote was PASS. The `route` skill was invoked once in each with-plugin run of the hard case, but its shell command was denied every time, in those runs and in two of the trivial ones: the eval runner removes Bash unless `--allow-tools` grants it, and a skill's own `allowed-tools` can't widen that (https://code.claude.com/docs/en/plugin-evals.md#grant-tools). So the with-plugin arm got the skill's text and the plugin's hooks, not the policy table or `explain` output. That and the fact that the model without the plugin also recommends a strong model for the migration and Haiku for the lookup explain the delta of 0: these evals show only that the plugin doesn't make the advice worse. Two of the three no-plugin runs of the trivial case took 3 turns and cost about $0.41 each, against $0.07 with the plugin; with three runs that is a hint, not a measured saving. The benchmark's pilot runs are in [BENCHMARKS.md](BENCHMARKS.md).

## Dogfood ledger

This repository's own sessions run with the plugin loaded. This ledger was read on the design before this one, which scored each brief for its level and raised the main loop's effort; both are deleted, and no ledger of the current tree has been read. The ledger (`~/.claude/plugins/data/model-router-claude-code-model-router/ledger.jsonl`) was read from a copy taken 2026-11-22 at 12:07 UTC: 400 rows from 07:16 to 12:06 UTC, across 16 sessions, all parsed. Only counts are recorded here, never brief text.

- 45 spawns, all on the mod path (`agent.spawn`): 25 `model-router:reviewer`, 14 `model-router:verifier`, 4 `general-purpose`, 1 `model-router:docs-researcher` and 1 `web-fetch`.
- 40 were kept unchanged: these sessions set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, which pins every subagent's model, and each role agent then set its own `effort` (reviewer `high`, verifier `low`, docs-researcher `medium`), so the router had nothing to set. Levels: 22 complex, 14 standard, 2 hardest, 1 frontier, 1 lookup.
- 5 were rewritten: 3 `general-purpose` lookups went from Sonnet to Haiku at `low` and ran on `claude-haiku-5-5`, 1 hardest `general-purpose` call from Sonnet to Opus at `xhigh`, and under the force setting 1 `web-fetch` lookup kept its model and got `low` effort.
- Claude Code resolved each call's asked model to that family's ID (3 Haiku, 42 Opus). 44 calls ran in the background and 1 in the foreground. No spawn's reason says it ran a level above an earlier run, so there was no redo.
- 12 main-loop turns were raised: 11 complex turns to `high` and 1 hardest turn to `xhigh`.
- 298 usage rows, every one priced, $439.99 at list price: the main loops $409.30 (246 rows) and subagents $30.69 (52 rows).

No routing error was found. Most spawns ran under the force setting, so this ledger mostly checks that path; model choice was exercised on 4 calls.

## Type-checking the mod

`sh tests/mod/typecheck.sh` is the gate, and CI's `typecheck` job runs it. It downloads Claude Code's published mod API types (https://github.com/anthropics/claude-code/blob/main/mods/types/claude-code.d.ts) at a pinned commit, installs `typescript` 6.0.3 and `@types/node` 22.20.5 into a temporary folder, and runs `tsc` with the options in `tests/mod/tsconfig.json`. The published copy can be older than your Claude Code (https://code.claude.com/docs/en/plugins/mods/reference.md); move the pin when the mod uses something newer. The daily `newest` job in `upstream-watch.yml` also runs it unpinned, as `TYPES_COMMIT=main sh tests/mod/typecheck.sh`, against the newest published types.

To check against the types your installed build writes, copy `tests/mod/tsconfig.json` outside the plugin folder, add `"paths": {"claude-code": ["<plugin-authoring skill>/types/claude-code.d.ts"]}` and `"files": ["<repo>/plugins/model-router/hooks/router.ts", "<repo>/tests/mod/router.test.ts"]`, install `@types/node` beside it, and run `tsc -p` on it, with `"types": ["node"]` and no DOM library. The 2.1.330 types redeclare `URL`, `URLSearchParams` and other globals in ways `@types/node` rejects, so pass `--skipLibCheck`; then `hooks/router.ts` and `tests/mod/router.test.ts` are clean.

## Benchmark raw data

[BENCHMARKS.md](BENCHMARKS.md) is generated by `python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md`. Notes about the raw lines that the script doesn't write go here. Every stored row measured a design before this one; the current tree has no benchmark rows.

Each raw line records `bench_commit`, the HEAD it ran on, and `bench_dirty`, whether `bench/` or the plugin had changes HEAD doesn't hold. When the `m6-*` files were recorded, the check also counted the bench's own output under `bench/results/`, so once a session's first results file was written, every later line said `bench_dirty: true`. That is why the 320 main rows of the `m6-*` files carry it while the 16 probe rows before them don't. The code that ran for all of them is `499d046`; the check now leaves `bench/results/` out.

Run numbers of reruns: a retried run keeps its own number. The six outage runs under `bench/results/archive/early-builds/infra/` were rerun by hand with `--runs 1`, so each new line says run 0; for four of them that is the slot they replace. The two `cross-currency` reruns (`plugin-cheap` and `plugin-cheap-nomain`, in `bench/results/archive/early-builds/m6-05-cross-module-2026-11-22.jsonl`) replace run 3, so those two cells list run 0 twice and no run 3. Run numbers only set the configuration order, so no figure on the page changes.

Wall time: rows from builds before `27b94e6`, under `bench/results/archive/early-builds/`, carry no `wall_ms`, and their `duration_ms` isn't used in its place. One process writes each results file, one run after another, so the gap between a run's `started_at` and the next is that run plus its check and setup. On the Opus-session files (`m6-05-opus-*`, `m6-05-confirm`), that gap minus `duration_ms` has a median of 0.9 to 1.1 s per file, about what check and setup take. On the Sonnet-session files the median is 1.1 to 36.5 s, and the gap is over 10 s more than `duration_ms` for 5 to 36 runs per file, so there `duration_ms` leaves out time the run took (the docs count the wait for background subagents in `duration_api_ms`, https://code.claude.com/docs/en/agent-sdk/cost-tracking.md). Rows from `27b94e6` on carry `wall_ms`, the runner's own clock around the `claude` process, and the page's latency comes from it.

All runs below used Claude Code 2.1.330 and had `bench_dirty` false on every row. Every cell reached its run count, and no run lost the network or hit a limit.

- The `27b94e6` run (2026-11-22, bench and router at `27b94e6`): `--runs 5` per task, so 10 per class and configuration, at `--budget 1.0`. Stage 1, an Opus 5.5 session (`--model claude-opus-5-5`), `plugin` and `defaults` on all 16 tasks: 160 runs, $21.88. Stage 2, a Sonnet session (`--model sonnet`), `plugin`, `defaults`, `sonnet-medium` and `sonnet-high`: 320 runs, $22.66. The total is $44.54 at list price. Each class ran as its own `run.py`, in parallel workers (2 on Opus, 4 on Sonnet) that shared the stage's cap ($30 and $36). The 810 earlier rows went to `bench/results/archive/`, so the tables compare the router at `27b94e6` only with baselines run beside it, on the same commit, task revisions and Claude Code version.
- The `780ee86` run (2026-11-22 UTC): `plugin-next`, the plugin exported from `780ee86`, which differs from 0.1.0 in its license, a state-file fix and the architect's description, on every task, `--runs 5`, so 10 per class. Sonnet session: 80 runs, $6.01, under an $8 cap. Opus 5.5 session: 80 runs, $9.55, under a $12 cap. The total is $15.56 at list price. The baselines weren't rerun: they are the `27b94e6` run's rows, on the same version, machine and task revisions. These runs used `--budget 0.6` where the baselines used 1.0, so `run.py`'s reserve of twice the budget fit one worker per stage under its cap; no `27b94e6` run had cost more than $0.37, so the lower cap ended no run. Command: `python3 bench/run.py --tasks <the class's task ids> --configs plugin-next --runs 5 --budget 0.6 --max-total <cap> --model sonnet --out bench/results/<name>.jsonl` (then `--model claude-opus-5-5`), one class at a time.
- The baseline run (2026-11-23): `haiku`, `haiku-high`, `opus`, `opus-high`, `fable`, `fable-high`, `opusplan` and the advisor on an Opus 5.5 session (`--model claude-opus-5-5`), every task, 10 runs per class, `--budget 1.0`. That's 640 runs, $94.83, plus $0.22 for one run each of the advisor and `opusplan` first to check this account can run them, so $95.06 under a $100 cap. The dearest run cost $0.54 (`fable-high` on `cross-currency`), and none errored. It ran in rounds (`--first-run`), so a cap stop could never leave a cell uneven: one round of every task under all eight, then four more for seven of them, then `fable-high`'s four. `opusplan` sets its own session model, so its rows join both session tables on the page; headless, it never enters plan mode and runs Sonnet throughout.
