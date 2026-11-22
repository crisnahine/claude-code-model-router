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

Run 2026-11-22 on macOS (Darwin 27.2.0), on the unreleased commits after `v0.1.0`, up to `fc90093` plus the change that updated this file.

| Gate | Command | Result |
| :- | :- | :- |
| Unit tests, Python 3.9.6 | `python3 -m unittest discover -s tests` | 297 tests OK |
| Mod tests, Node 22.22.3 | `node --experimental-strip-types --test "tests/mod/*.test.ts"` | 18 of 18 pass |
| Marketplace, Claude Code 2.1.330 | `claude plugin validate --strict .` | Validation passed |
| Plugin, Claude Code 2.1.330 | `claude plugin validate --strict plugins/model-router` | Validation passed |
| Mod type check against the installed build's types, `tsc` 6.0.3 | `tsc -p <dir>/tsconfig.json` | Not rerun on this commit |
| Mod and its test, `tsc` 6.0.3 | `sh tests/mod/typecheck.sh` (published types at a pinned commit, `@types/node` 22.20.5) | No errors |
| Traceability count | `python3 tools/traceability.py` | Matches the summary line in [TRACEABILITY.md](TRACEABILITY.md) |

The 297 local tests are 263 in `tests/test_model_router.py`, 28 in `tests/test_bench.py`, 3 in `tests/test_routing_corpus.py` and 3 in `tests/test_traceability.py`. The routing corpus test prints "2 of 219 briefs over-routed (0.9%), budget 2%; 5 known under-routes". CI's run on `8ec8eec` had 180.

Not run: `claude plugin eval plugins/model-router --trust-plugin` (it runs live models and spends money, and has not been rerun since the routing evals were written), and live sessions (the last ones are in `docs/ARCHITECTURE.md` "Verified live"). The benchmark's pilot runs are in [BENCHMARKS.md](BENCHMARKS.md).

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
