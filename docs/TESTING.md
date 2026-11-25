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
| Plugin validate | `claude plugin validate --strict .` and `claude plugin validate --strict plugins/model-router` | The marketplace and the plugin, warnings as errors, on Claude Code 2.1.329, 2.1.331 and latest. |
| Version | `python3 tools/plugin_version.py origin/main` | A plugin change sets a new version. |
| Docs | `python3 tools/docs_assets.py --check` | Every file the script writes is current. |
| Benchmarks page | `python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check` | `docs/BENCHMARKS.md` matches `bench/results/`. |
| Live evals | `claude plugin eval plugins/model-router --trust-plugin` | Claude names the expected model and effort on real runs. Paid, so run after a routing change. |

## The interactive features by hand

The status line, `/model-router` and the session check run only in a session with a person at the prompt, which no gate above starts. Checked by hand in the terminal on Claude Code 2.1.332: `session.start` reports `isInteractive` and no source, and fires again on every plugin reload, an option change by `$.config.set` included; `$.ui.status` draws under the prompt after the plugin's name and updates; a mod command named `model-router` lists in the typeahead and runs with no model turn; `$.ui.ask` from a task started at `session.start` shows before the first prompt and adds its own "Type something" and "Chat about this" rows; `$.command.run` for `model` and `effort` switches the running session and saves both as defaults; `$.session.model()` answers the full model ID; `$.config.list()` keys the option `model-router.session_check` and `$.config.set` writes it to `pluginConfigs`.

Still to check on a live run:

1. The mod, with these calls, loads on Claude Code 2.1.329 and 2.1.330.
2. The status line, the command, the dialog and the two commands with arguments in the Desktop app's Code tab and the VS Code extension, and what `isInteractive` reports there.
3. `$.session.turns()` is 0 at the `session.start` of a fresh launch and above 0 under `--resume`, `--continue` and `--fork-session`.
4. A first prompt given at launch starts its turn before the question shows, so no dialog appears mid-work.
5. `/model-router` typed while a turn runs: whether Claude reads its reply.
6. The toast after "Don't ask again", sent after `$.config.set` has reloaded the plugin.
7. A split-pane teammate process and a background session: whether they report `isInteractive` and would ask.
8. A refused `/model` from the mod (Fable's usage-credits prompt dismissed, a blocked model, a `PreModelSwitch` hook): `$.session.model()` keeps the old model, and the check says the session stayed.
9. `/effort max` from the mod lasts the session only.
10. Whether a `/model` before the first prompt shows a cache confirmation.
11. Whether adding `session_check` opens the options dialog for an existing install.
12. `isLocked` on the `session_check` row under a managed `pluginConfigs` value.
13. The estimate against the first request's effort under a `--effort` flag and an organization default.
14. Whether the status line goes away when the plugin is disabled or reloaded.
15. The header chip `Model Router` on the dialog.
16. What `$.session.model()` answers under `opusplan` and `default`.

## Latest results

Not filled in yet: the date, the commit and each gate's result line go here once the gates run on the release commit.
