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

Run 2026-11-22 on macOS (Darwin 27.2.0), on the unreleased commits after `v0.1.0`, up to `ea8bf9a` plus the change that updated this file.

| Gate | Command | Result |
| :- | :- | :- |
| Unit tests, Python 3.9.6 | `python3 -m unittest discover -s tests` | 296 tests OK |
| Mod tests, Node 22.22.3 | `node --experimental-strip-types --test "tests/mod/*.test.ts"` | 18 of 18 pass |
| Marketplace, Claude Code 2.1.330 | `claude plugin validate --strict .` | Validation passed |
| Plugin, Claude Code 2.1.330 | `claude plugin validate --strict plugins/model-router` | Validation passed |
| Mod type check against the installed build's types, `tsc` 6.0.3 | `tsc -p <dir>/tsconfig.json` | Not rerun on this commit |
| Mod and its test, `tsc` 6.0.3 | `sh tests/mod/typecheck.sh` (published types at a pinned commit, `@types/node` 22.20.5) | No errors |
| Traceability count | `python3 tools/traceability.py` | Matches the summary line in [TRACEABILITY.md](TRACEABILITY.md) |
| Plugin evals, Claude Code 2.1.330 | `claude plugin eval plugins/model-router --trust-plugin --ablation with-without --runs 3 --max-cost-usd 3 --no-publish` (below) | 2 of 2 cases pass, with and without the plugin |

The 296 local tests are 262 in `tests/test_model_router.py`, 28 in `tests/test_bench.py`, 3 in `tests/test_routing_corpus.py` and 3 in `tests/test_traceability.py`. The routing corpus test prints "2 of 219 briefs over-routed (0.9%), budget 2%; 5 known under-routes". CI's run on `8ec8eec` had 180.

Not run here: live sessions (the last ones are in `docs/ARCHITECTURE.md` "Verified live").

## Plugin evals

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
