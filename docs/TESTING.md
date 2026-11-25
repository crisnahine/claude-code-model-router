<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="BENCHMARKS.md">Benchmarks</a> &nbsp;&middot;&nbsp; Next: <a href="ROADMAP.md">Roadmap</a></p>
<!-- End of page head. -->

# Testing

Every change passes these gates before it is merged. CI (`.github/workflows/ci.yml`) runs all but the live evals on every pull request and push to `main`.

| Gate | Command | What it checks |
| :- | :- | :- |
| Unit tests | `python3 -m unittest discover -s tests` | The runtime and hooks, the eval cases offline, the generated docs and the benchmarks page, on Linux, macOS and Windows, Python 3.9 and 3.13. |
| Mod tests | `node --experimental-strip-types --test "tests/mod/*.test.ts"` | The mod, `hooks/router.ts`, on Node 22, on the same three systems. |
| Type check | `sh tests/mod/typecheck.sh` | The mod and its test against Claude Code's mod API types, pinned to a commit. |
| Plugin validate | `claude plugin validate --strict .` and `claude plugin validate --strict plugins/model-router` | The marketplace and the plugin, warnings as errors, on Claude Code 2.1.329, 2.1.331 and the newest release. |
| Version | `python3 tools/plugin_version.py origin/main` | A pull request that changes the plugin sets a new version. |
| Docs | `python3 tools/docs_assets.py --check` | Diagrams, badges, page heads, the docs index, the site and the benchmark figures match the script. |
| Benchmarks page | `python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check` | `docs/BENCHMARKS.md` matches `bench/results/`, and the facts the docs state about it still hold. |
| Live evals | `claude plugin eval plugins/model-router --trust-plugin` | Claude names the expected model and effort on real runs. It costs money, so CI runs it by hand and weekly. Rerun it after a routing change. |

## Latest results

Not filled in yet: the date, the commit and each gate's result line go here once the gates run on the release commit.
