<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="CHANGELOG.md">Changelog</a> &nbsp;&middot;&nbsp; Next: <a href="RELEASING.md">Releasing</a></p>
<!-- End of page head. -->

# Contributing

## Where to work

Branch from `main` and work in a git worktree (`git worktree add ../<name> -b <name> main`), so the main checkout keeps its branch. CI runs on pushes to `main` and on pull requests, so a branch is tested once it has an open PR.

## Repository layout

```text
plugins/model-router/
  .claude-plugin/plugin.json   manifest and options
  config/models.json           the models, aliases, newest IDs, lagging provider aliases, levels and prices
  hooks/router.ts              the mod: tool.call, session.start, agent.spawn, turn.start, turn.step,
                               turn.complete
  hooks/hooks.json             loads the mod, and the SessionStart, UserPromptSubmit, PreToolUse,
                               PostToolUse, PostToolUseFailure, PostModelSwitch, SubagentStop and Stop hooks
  scripts/model-router.sh      the launcher hooks and skills run: python3, else python, 3.9 or later, with -I
  scripts/modelrouter/         the runtime, standard library only
  agents/                      the five role agents
  skills/                      route, models, usage
  evals/                       routing evals for claude plugin eval
.claude/skills/                refresh and deliver, maintainer skills for this repository (not shipped)
tests/                         unit tests, run on Linux, macOS and Windows; tests/mod holds the mod's tests
compat/baseline.json           the Claude Code version, docs pages, model aliases and model status last reviewed
docs/                          the pages indexed in docs/README.md, docs/research and docs/assets (BRAND.md)
tools/                         traceability.py counts docs/TRACEABILITY.md rows; docs_assets.py writes the docs' generated files;
                               docs_pages.py is the page index and page heads; plugin_version.py is CI's version check;
                               upstream_check.py is the daily upstream check
bench/                         the benchmark: pinned tasks and their pass checks, configurations, runner and analysis,
                               and overhead.py for hook time and state size (not shipped)
site/                          the project site, sitemap and llms.txt; a workflow copies in docs/assets and deploys it to GitHub Pages from main
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the design and the reasons behind it. A daily GitHub workflow runs `tools/upstream_check.py`, which compares live Claude Code releases, docs pages, model aliases and model status with `compat/baseline.json` and opens an issue when something this plugin is built on changes. `/refresh` in this repository reviews the changes, and `python3 tools/upstream_check.py --baseline compat/baseline.json --write` marks them reviewed.

## Gates

Every change passes these before it is merged:

```bash
python3 -m unittest discover -s tests
node --experimental-strip-types --test "tests/mod/*.test.ts"
claude plugin validate --strict .
claude plugin validate --strict plugins/model-router
python3 tools/traceability.py
sh tests/mod/typecheck.sh
python3 tools/docs_assets.py --check
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check
```

CI runs all of them: the unit tests include the traceability count, `docs_assets.py --check` and the benchmarks-page check.

- `bench/analyze.py ... --check` fails when `docs/BENCHMARKS.md` no longer matches the rows in `bench/results/`, or when a fact the docs' prose states about them (every run solved, a baseline cheaper in every class, no class with a verdict) is false now. Rebuild the page by running it without `--check`, and fix the prose when a fact fails.
- `sh tests/mod/typecheck.sh` type-checks the mod and its test with `tsc` against Claude Code's published mod API types, pinned to a commit, using the tsconfig in `tests/mod`. `TYPES_COMMIT=main sh tests/mod/typecheck.sh` checks against the newest published types instead, as the daily upstream workflow does. To check against the types your installed Claude Code writes, see [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md).
- A change to routing reruns `claude plugin eval plugins/model-router --trust-plugin`. It runs live models, so record what it cost. CI's `eval` job runs it on Claude Code 2.1.331 when started by hand from the Actions tab, and on the newest release once a week, with the repository's `ANTHROPIC_API_KEY` secret.

The routing evals also run free in the unittest gate. Each case under `plugins/model-router/evals` names in `expected_outcome` the model and effort Claude should name for its work (`model M, effort E`, with `agent A, ` in front when the case checks which agent Claude picks). `RoutingEvals` in `tests/test_model_router.py` checks that the session note pairs that model with that effort, that a grader checks that model (and that agent, and the model Claude names when that agent sets none), and that `spawn.decide` runs a call naming both on them, in a session on the case's model. Whether Claude names them, and the redo's climb in `route-redo-escalates`, are checked only by the live run. To run only that check: `python3 -m unittest tests.test_model_router.RoutingEvals`. A change to the note's table updates the cases' `expected_outcome` and graders in the same commit.

## Rules for a change

- The runtime is Python 3.9 or later on the standard library only. Never add a dependency.
- The mod stays thin: it forwards events to the runtime and picks no model or effort of its own.
- Every hook fails open. A bug may cost routing, never a tool call, a spawn or a session.
- A claim about Claude Code or a model comes from the newest official docs (`code.claude.com/docs`, `platform.claude.com/docs`) or the installed binary, with the link. Anything without a primary source is marked UNCONFIRMED in `docs/research/` and nothing is built on it.
- A change updates every document it makes wrong, in the same commit. When it changes a requirement's status, update its row in [docs/TRACEABILITY.md](docs/TRACEABILITY.md), run `python3 tools/traceability.py`, and paste its output over the "Summary:" line. `tests/test_traceability.py` fails until they agree.
- A new module or concept gets a term in [GLOSSARY.md](GLOSSARY.md).
- The diagrams, badges, page heads, the docs index, the site's FAQ and `CITATION.cff` come from `tools/docs_assets.py`: change the script, run it, and keep `--check` passing. It also fills the benchmark figures marked `<!-- bench:KEY -->value<!-- /bench -->` (in the README, the docs pages that carry them, the site and `site/llms.txt`) from `bench/analyze.py`, so a figure changes by rerunning analyze, never by hand. [docs/assets/BRAND.md](docs/assets/BRAND.md) has the palette and how to add a page.
- Every change that reaches `main` under `plugins/model-router/` sets a version in `plugin.json` that `main` has not carried since its first release (`v0.1.0`), since installs keep their cached copy until the version changes. CI's `version` job (`tools/plugin_version.py`) fails a pull request that keeps a version the base branch has carried since its first `v*` tag or one tagged `v<version>`; a change to `plugins/model-router/README.md` alone is exempt.
- A change to the shape of `config/models.json`, a `model-router.json` override, a state file, a `--json` output or a plugin option follows the policy in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#compatibility): a breaking change bumps that shape's version and keeps the old one readable.
- Code comments explain a reason the code can't show, in a line. No ticket numbers, no history of the change.

## Commits

- One problem per commit, with its tests and docs.
- The subject is a plain sentence naming what changed: "Arm the unverified-edit gate only for files in the project". No type prefix.
- The body says what was wrong and why: the cause, then what the change does about it.
- No AI or tool attribution anywhere: no `Co-Authored-By` trailer for a tool, no "Generated with" footer, in commits, PRs or files.
- Never force-push `main`.

## License of contributions

Contributions are licensed under Apache-2.0, the same as the project (section 5 of the license). No CLA or sign-off is required.

## Releases

[RELEASING.md](RELEASING.md) is the checklist, worked in order. The maintainer merges, tags and publishes.
