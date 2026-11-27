<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="BENCHMARKS.md">Benchmarks</a> &nbsp;&middot;&nbsp; Next: <a href="ROADMAP.md">Roadmap</a></p>
<!-- End of page head. -->

# Testing

Every change passes these gates before merging. CI (`.github/workflows/ci.yml`) runs them on each pull request and push to `main`. The version check runs on pull requests only, and the live evals by hand and weekly.

| Gate | Command | What it checks |
| :- | :- | :- |
| Unit tests | `python3 -m unittest discover -s tests` | The runtime, the eval cases offline, and the docs and benchmarks-page gates, on Linux, macOS and Windows with Python 3.9 and 3.13. |
| Mod tests | `node --experimental-strip-types --test "tests/mod/*.test.ts"` | The mod on Node 22, on the same three systems. |
| Type check | `sh tests/mod/typecheck.sh` | The mod and its test against Claude Code's mod API types, pinned to a commit. |
| Plugin validate | `claude plugin validate --strict .` and `claude plugin validate --strict plugins/model-router` | The marketplace and the plugin, warnings as errors, on Claude Code 2.1.329, 2.1.332 and latest. |
| Version | `python3 tools/plugin_version.py origin/main` | A plugin change sets a new version. |
| Docs | `python3 tools/docs_assets.py --check` | Every file the script writes is current. |
| Benchmarks page | `python3 bench/analyze.py "bench/results/*.jsonl" --out docs/BENCHMARKS.md --check` | `docs/BENCHMARKS.md` matches `bench/results/`. |
| Live evals | `claude plugin eval plugins/model-router --trust-plugin` | Claude names the expected model and effort on real runs. Paid, so run after a routing change. |

## The interactive features by hand

The status line, `/model-router` and the session check run only in a session with a person at the prompt, which no gate above starts. They are checked by hand in a terminal session, with the plugin loaded by `--plugin-dir` and a new config folder for each case:

| Case | What happens | Claude Code |
| :- | :- | :- |
| A session on Haiku | The question shows before the first prompt, with the `Model Router` chip and the four options | 2.1.329, 2.1.332 |
| Opus, then `medium` | `/model opus` runs alone, since the effort already holds | 2.1.332 |
| Fable, then `max` | `/model fable`, then `/effort max`, which Claude Code applies to that session only | 2.1.332 |
| A session on Sonnet at `medium`; Sonnet, then `xhigh` | `/effort xhigh` runs alone | 2.1.332 |
| Stay, then "Don't ask again" | The option is written, the plugin reloads, and nothing asks again in that session | 2.1.332 |
| Esc at the first step | Nothing changes | 2.1.332 |
| A session on Opus | No question | 2.1.332 |
| `--model sonnet --effort xhigh` | No question; `/model-router` says `session: sonnet at xhigh` | 2.1.332 |
| `--model opus --effort low` | The question names Opus at low | 2.1.332 |
| A prompt given at launch | No question, during the turn or after it | 2.1.332 |
| `--continue` on that session | No question | 2.1.332 |
| One subagent call | The line under the prompt goes from `auto · mod · 0 calls` to `auto · mod · 1 call · 1 changed` | 2.1.332 |
| `/model-router`, `/model-router off`, `/model-router check on` | Each replies with no model turn, and the line under the prompt follows | 2.1.332 |

Not run by hand: the Desktop app's Code tab and the VS Code extension's panel (the Claude Code each bundles, 2.1.330 and 2.1.331, routes a call on both paths in a headless run), the web, a split-pane teammate and a background session, `--resume` and `--fork-session`, `/model-router` typed while a turn runs, a `/model` that Claude Code refuses, a `session_check` that managed settings own, an organization's default effort, and a `/config` change refused while a routed agent runs.

## Latest results

On 2026-11-25, at commit `4659b73`, the commit the judged benchmark rounds ran on, in [CI run 45641894749](https://github.com/crisnahine/claude-code-model-router/actions/runs/45641894749) unless a row says otherwise. The commits after it add those rounds' rows and numbers, and CI runs the same gates on each:

| Gate | Result |
| :- | :- |
| Unit tests | 867 run and pass on Ubuntu, macOS and Windows, each with Python 3.9 and 3.13. 2 are skipped, 20 on Windows. |
| Mod tests | 71 pass on the same three systems. |
| Type check | Passes. |
| Plugin validate | Passes on Claude Code 2.1.329, 2.1.331 and the newest release, 2.1.332. |
| Version, docs, benchmarks page | Pass. |
| Live evals | Run by hand on Claude Code 2.1.332 with an API key, at `0f23016`: 0.98 against the 0.8 threshold, 5 of 5 cases pass, 14 of 15 runs pass. The same cases without the plugin score 0.21. |

Live runs of the plugin during this release, each a headless session that made an Agent call:

| Where | What ran |
| :- | :- |
| macOS, Claude Code 2.1.332, an API key | The 300 runs of the judged benchmark rounds, at `4659b73`. |
| The Claude Code inside the Desktop app (2.1.330) and the VS Code extension (2.1.331) | A call routed on the mod path and on the fallback path, on an earlier commit of this release. |
| A Linux container (Debian 12, Python 3.11) with Claude Code 2.1.332 | A call routed on each path, on an earlier commit of this release. |
| The Python Agent SDK 0.2.189 | A call routed through the mod, on an earlier commit of this release. |
