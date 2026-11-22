# Test results

The latest results, with the commands that produce them. Replace this file's numbers whenever a gate is rerun; never edit a number you didn't see.

## CI on `main`

`gh run list --branch main` shows one run: [45004998622](https://github.com/crisnahine/claude-code-model-router/actions/runs/45004998622), on `8ec8eec` (the `v0.1.0` merge), started 2026-11-22 08:54 UTC. Result: success, 8 of 8 jobs.

| Job | Result | Tests | Took |
| :- | :- | :- | -: |
| `test (ubuntu-latest, 3.13)` | success | 180 OK, 0 skipped | 13 s |
| `test (ubuntu-24.04, 3.9)` | success | 180 OK, 0 skipped | 17 s |
| `test (macos-latest, 3.9)` | success | 180 OK, 0 skipped | 79 s |
| `test (macos-latest, 3.13)` | success | 180 OK, 0 skipped | 52 s |
| `test (windows-latest, 3.9)` | success | 180 OK, 0 skipped | 80 s |
| `test (windows-latest, 3.13)` | success | 180 OK, 0 skipped | 35 s |
| `mod` (Linux) | success | 12 of 12 pass | 5 s |
| `validate` | success | marketplace and plugin both "Validation passed" | 12 s |

The test counts come from each job's log (`gh run view --job <id> --log`). Commits after `8ec8eec` have not run in CI yet. CI runs on pushes to `main` and on pull requests, so a work branch is covered once it has an open PR.

## Local

Run 2026-11-23 on macOS (Darwin 27.2.0), on the unreleased commits after `v0.1.0`, up to `241b465` plus the change that updated this file.

| Gate | Command | Result |
| :- | :- | :- |
| Unit tests, Python 3.9.6 | `python3 -m unittest discover -s tests` | 350 tests OK |
| Unit tests, the system Python 3.9.6 | `/usr/bin/python3 -m unittest discover -s tests` | 350 tests OK |
| Unit tests, Python 3.15.2 | `python3.15 -m unittest discover -s tests` | 350 tests OK |
| Mod tests, Node 22.22.3 | `node --experimental-strip-types --test "tests/mod/*.test.ts"` | 18 of 18 pass |
| Marketplace, Claude Code 2.1.330 | `claude plugin validate --strict .` | Validation passed |
| Plugin, Claude Code 2.1.330 | `claude plugin validate --strict plugins/model-router` | Validation passed |
| Mod and its test, `tsc` 6.0.3 | `sh tests/mod/typecheck.sh` (published types at a pinned commit, `@types/node` 22.20.5) | No errors |
| Traceability count | `python3 tools/traceability.py` | Matches the summary line in [TRACEABILITY.md](TRACEABILITY.md) |
| Plugin evals, Claude Code 2.1.330 | `claude plugin eval plugins/model-router --trust-plugin --ablation with-without --runs 3 --max-cost-usd 2 --no-publish` (below) | 3 of 3 cases pass with the plugin and none without, mean delta 0.78; run 2026-11-22, not rerun on this commit |

The 350 local tests are 299 in `tests/test_model_router.py`, 45 in `tests/test_bench.py`, 3 in `tests/test_routing_corpus.py` and 3 in `tests/test_traceability.py`. The routing corpus test prints "2 of 222 briefs over-routed (0.9%), budget 2%; 5 known under-routes" on each of the three interpreters. CI's run on `8ec8eec` had 180.

Not run here: live sessions (the last ones are in `docs/ARCHITECTURE.md` "Verified live").

## Plugin evals

### Routing cases

Run 2026-11-22 on Claude Code 2.1.330, on the three cases now in `plugins/model-router/evals/`:

```bash
CLAUDE_CODE_PLUGIN_CACHE_DIR=<scratch>/plugins claude plugin eval plugins/model-router --trust-plugin \
  --ablation with-without --runs 3 -j 2 --max-cost-usd 2 --no-publish --output-dir <scratch>/out --json <scratch>/result.json
```

Same clean environment as below. Each case tells Claude to make one `Agent` call with an exact brief and no model, and a `regex` grader reads the run's trace for the `resolvedModel` Claude Code reports on that call's result. Without the router, a subagent with no model runs on the session's model, so the no-plugin arm fails by design. No `--allow-tools` grant is needed: routing runs in the plugin's hooks and mod, which start their own processes, not through the Bash tool.

| Case | Session | Grader | With the plugin | Without | Cost with / without (3 runs each) |
| :- | :- | :- | -: | -: | -: |
| `route-lookup-subagent` | Sonnet | `ran-on-haiku` | 1.0, 3 of 3 (Haiku each run) | 0, 0 of 3 (Sonnet each run) | $0.11 / $0.14 |
| `route-review-subagent` | Haiku | `ran-on-opus` | 1.0, 3 of 3 (Opus each run) | 0, 0 of 3 (Haiku each run) | $0.10 / $0.01 |
| `route-redo-escalates` | Haiku | `called-twice`, `first-ran-on-haiku`, `redo-ran-on-sonnet` | 1.0, 3 of 3 (Haiku, then Sonnet) | 0.67, 0 of 3 (Haiku twice) | $0.09 / $0.01 |

Total $0.43 (no LLM grader, so no judge cost), 139 seconds, not partial. `first-ran-on-haiku` was added afterwards and only `route-redo-escalates` was rerun (`--case route-redo-escalates`, $0.10, 60 seconds); its row shows that rerun. The grader checks that a run on Haiku is in the trace, and in every run the Haiku result came first. With it the case's delta is 0.33, so the suite's mean delta is 0.78. The trace carries each subagent's model but not its effort, and the spend log lives in the plugin's data folder, which a grader can't read (https://code.claude.com/docs/en/plugin-evals.md#what-a-grader-can-look-at), so effort and spend rows are covered by the unit tests and the live runs in `docs/ARCHITECTURE.md`, not here.

### The first run, on the cases these replaced

Run 2026-11-22 on Claude Code 2.1.330 against `atd-7385-next`:

```bash
CLAUDE_CODE_PLUGIN_CACHE_DIR=<scratch>/plugins claude plugin eval plugins/model-router --trust-plugin \
  --ablation with-without --runs 3 -j 2 --max-cost-usd 3 --no-publish --output-dir <scratch>/out --json <scratch>/result.json
```

It ran in a clean environment (no `CLAUDECODE`, `CLAUDE_CODE_*` or `CLAUDE_CODE_SUBAGENT_MODEL`), with a scratch plugins root so the shared plugin data folder wasn't touched, and with the report kept local (`--no-publish`; by default the CLI publishes it to claude.ai). No `--model` was set, so each case ran on the account's default model; the LLM graders used the default judge, Haiku, with three votes each. The `with-without` arm runs each case once with the plugin and once without (https://code.claude.com/docs/en/plugin-evals.md).

| Case | Graders | With the plugin | Without | Cost with / without (3 runs each) |
| :- | :- | -: | -: | -: |
| `route-hard-task` | `picks-strong-tier` (LLM), `skill-fired` (with-only: the `route` skill was invoked) | 1.0, 3 of 3 | 1.0, 3 of 3 | $0.28 / $0.23 |
| `route-trivial-task` | `picks-cheap-tier` (LLM), `no-fable` (regex) | 1.0, 3 of 3 | 1.0, 3 of 3 | $0.22 / $0.88 |

Total $1.61 including the judge, 148 seconds, not partial. Every judge vote was PASS. The `route` skill was invoked once in each with-plugin run of the hard case, but its shell command was denied every time, in those runs and in two of the trivial ones: the eval runner removes Bash unless `--allow-tools` grants it, and a skill's own `allowed-tools` can't widen that (https://code.claude.com/docs/en/plugin-evals.md#grant-tools). So the with-plugin arm got the skill's text and the plugin's hooks, not the router's policy table or `explain` output. That is part of why the score delta is 0 for both cases. The rest is that the model without the plugin also recommends a strong model for the migration and Haiku for the lookup, so these two evals check that the plugin doesn't make the advice worse, not what it adds. Two of the three no-plugin runs of the trivial case took 3 turns and cost about $0.41 each, against $0.07 with the plugin; with three runs that is a hint, not a measured saving. The benchmark's pilot runs are in [BENCHMARKS.md](BENCHMARKS.md).

## Dogfood ledger

The team that works on this repository runs with the plugin loaded, so its own subagents go through the router. Its ledger is `~/.claude/plugins/data/model-router-claude-code-model-router/ledger.jsonl`. It was read from a copy taken 2026-11-22 at 12:07 UTC: 400 rows from 07:16 to 12:06 UTC, across 16 sessions, all of them parsed. Only counts are recorded here, never brief text.

- 45 spawns, all on the mod path (`agent.spawn`). 25 were `model-router:reviewer`, 14 `model-router:verifier`, 4 `general-purpose`, 1 `model-router:docs-researcher` and 1 `web-fetch`.
- 40 were kept unchanged. These sessions set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, which pins every subagent's model, and each of these role agents sets its own `effort` (reviewer `high`, verifier `low`, docs-researcher `medium`), so the router had nothing left to set. Their levels: 22 complex, 14 standard, 2 hardest, 1 frontier and 1 lookup.
- 5 were rewritten. 3 `general-purpose` lookups went from Sonnet to Haiku at `low` and ran on `claude-haiku-5-5`. 1 hardest `general-purpose` call went from Sonnet to Opus at `xhigh`. Under the force setting, 1 `web-fetch` lookup kept its model and got `low` effort.
- Each call's `result` row named the model it asked for, and Claude Code resolved each one to that family's ID (3 Haiku, 42 Opus). 44 calls ran in the background and 1 in the foreground.
- No redo: no spawn's reason says it ran a level above an earlier run.
- 12 main-loop turns were raised: 11 complex turns to `high` and 1 hardest turn to `xhigh`.
- 298 usage rows, every one priced, $439.99 at list price. The main loops account for $409.30 (246 rows) and subagents for $30.69 (52 rows).

No routing error was found. Most spawns ran under the force setting, so this ledger mostly checks that path. Model choice was exercised on 4 calls only.

## Type-checking the mod

`sh tests/mod/typecheck.sh` is the gate, and CI's `typecheck` job runs it. It downloads Claude Code's published mod API types (https://github.com/anthropics/claude-code/blob/main/mods/types/claude-code.d.ts) at a pinned commit, installs `typescript` 6.0.3 and `@types/node` 22.20.5 into a temporary folder, and runs `tsc` with the options in `tests/mod/tsconfig.json`. The published copy can be older than your Claude Code (https://code.claude.com/docs/en/plugins/mods/reference.md); move the pin when the mod starts using something newer.

To check against the types your installed build writes, keep a tsconfig outside the plugin folder, so nothing extra ships. Point `claude-code` at the types the plugin-authoring skill writes for the installed build, and leave the DOM library out, since those types declare their own globals:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noEmit": true,
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "types": [],
    "paths": { "claude-code": ["<plugin-authoring skill>/types/claude-code.d.ts"] }
  },
  "files": ["<repo>/plugins/model-router/hooks/router.ts"]
}
```

To check the test as well, add `tests/mod/router.test.ts` to `files`, install `@types/node` next to the tsconfig, set `"types": ["node"]`, and pass `--skipLibCheck`. The 2.1.330 types redeclare `URL`, `URLSearchParams` and other globals in ways `@types/node` rejects, so the library check fails inside the two `.d.ts` files. With `--skipLibCheck` both `.ts` files are clean.

## Benchmark raw data

[BENCHMARKS.md](BENCHMARKS.md) is generated by `python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md`, so notes about the raw lines that the script doesn't write go here.

Run numbers of reruns: a run the runner retries keeps its own number. The six outage runs under `bench/results/infra/` were run again by hand with `--runs 1`, so each new line says run 0. For four of them that is the slot they replace. The two `cross-currency` reruns (`plugin-cheap` and `plugin-cheap-nomain`, in `bench/results/m6-05-cross-module-2026-11-22.jsonl`) replace run 3, so those two cells list run 0 twice and no run 3. Run numbers only set the configuration order, so no figure on the page changes.

Wall time: the committed rows carry no `wall_ms`, so the page's latency is n/a, and their `duration_ms` isn't used in its place. Each results file is written by one process, one run after another, so the gap between one run's `started_at` and the next is that run plus its check and setup. On the Opus-session files (`m6-05-opus-*`, `m6-05-confirm`), that gap minus `duration_ms` has a median of 0.9 to 1.1 s per file, which is about what the check and setup take. On the Sonnet-session files the median is 1.1 to 36.5 s per file, and the gap is more than 10 s over `duration_ms` for 5 to 36 runs per file. So on those rows `duration_ms` leaves out time the run took. The docs count the wait for background subagents in `duration_api_ms`, not in `duration_ms` (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md). Rows recorded from now on carry `wall_ms`, the runner's own clock around the `claude` process.
