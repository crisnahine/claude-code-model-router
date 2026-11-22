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

Run 2026-11-22 on macOS (Darwin 27.2.0), on the unreleased commits after `v0.1.0`, up to `5049780` plus the change that updated this file.

| Gate | Command | Result |
| :- | :- | :- |
| Unit tests, Python 3.9.6 | `python3 -m unittest discover -s tests` | 253 tests OK |
| Mod tests, Node 22.22.3 | `node --experimental-strip-types --test "tests/mod/*.test.ts"` | 17 of 17 pass |
| Marketplace, Claude Code 2.1.330 | `claude plugin validate --strict .` | Validation passed |
| Plugin, Claude Code 2.1.330 | `claude plugin validate --strict plugins/model-router` | Validation passed |
| Mod type check, `tsc` 6.0.3 | `tsc -p <dir>/tsconfig.json` | No errors |
| Mod and its test, `tsc` 6.0.3 | `tsc -p <dir>/tsconfig.json --skipLibCheck`, with `@types/node` 22 | 1 error: `tests/mod/router.test.ts(46,40): error TS2322: Type '(e: unknown) => unknown' is not assignable to type '(x: object) => object'` |
| Traceability count | `python3 tools/traceability.py` | Matches the summary line in [TRACEABILITY.md](TRACEABILITY.md) |

The 253 local tests are 231 in `tests/test_model_router.py`, 19 in `tests/test_bench.py` and 3 in `tests/test_traceability.py`. CI's run on `8ec8eec` had 180.

Not run: `claude plugin eval plugins/model-router --trust-plugin` (it runs live models and spends money, and has not been rerun since the routing evals were written), and live sessions (the last ones are in `docs/ARCHITECTURE.md` "Verified live"). The benchmark's pilot runs are in [BENCHMARKS.md](BENCHMARKS.md).

## Type-checking the mod

The tsconfig lives outside the plugin folder, so nothing extra ships. Point `claude-code` at the mod API types the plugin-authoring skill writes for the installed build, and leave the DOM library out, since those types declare their own globals:

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

To check the test as well, add `tests/mod/router.test.ts` to `files`, install `@types/node` next to the tsconfig, set `"types": ["node"]`, and pass `--skipLibCheck`. The 2.1.330 types redeclare `URL`, `URLSearchParams` and other globals in ways `@types/node` rejects, so the library check fails inside the two `.d.ts` files. With `--skipLibCheck` the mod is clean and the test has the one error above.
