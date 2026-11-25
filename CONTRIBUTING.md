<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="CHANGELOG.md">Changelog</a> &nbsp;&middot;&nbsp; Next: <a href="docs/RELEASING.md">Releasing</a></p>
<!-- End of page head. -->

# Contributing

## Set up

You need Python 3.9 or later, Node 22 and Claude Code 2.1.329 or later. The type check also needs `curl`, `npm` and the network, and installs `tsc` in a temporary folder.

Branch from `main`. A git worktree keeps your main checkout on its branch, if you want one. To try a change in a session, run `claude --plugin-dir ./plugins/model-router`. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the design.

## Checks

Every change passes these before it is merged:

```bash
python3 -m unittest discover -s tests
node --experimental-strip-types --test "tests/mod/*.test.ts"
sh tests/mod/typecheck.sh
claude plugin validate --strict .
claude plugin validate --strict plugins/model-router
python3 tools/plugin_version.py origin/main
python3 tools/docs_assets.py --check
python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check
```

CI runs all of them, the version check on pull requests only. The unit tests include the last two. [docs/TESTING.md](docs/TESTING.md) says what each checks.

- A change to routing also runs the paid live evals: `claude plugin eval plugins/model-router --trust-plugin`. CI's `eval` job runs them when started by hand and once a week.
- The unit tests check each eval case offline (`RoutingEvals`): the session note pairs the model and effort in its `expected_outcome`, and a call naming them runs on them. A change to the note's table updates the cases and their graders in the same commit.
- A daily workflow compares upstream Claude Code with `compat/baseline.json` and opens an issue on a change. Review it with `/refresh` in this repository, then run `python3 tools/upstream_check.py --baseline compat/baseline.json --write` in the same pull request.

## What a change must include

- Its tests, and an update to every page it makes wrong, in the same commit.
- A term in [docs/GLOSSARY.md](docs/GLOSSARY.md) for a new module or concept.
- A new version in `plugin.json` when it touches `plugins/model-router/` (its `README.md` aside). Installs keep their cached copy until the version changes, and CI's `version` job fails a reused one.
- A version bump on any shape it breaks: `config/models.json`, a `model-router.json` override, a state file, a `--json` output or a plugin option. The old shape stays readable.

## Rules

- The runtime is Python 3.9 or later on the standard library only. No dependencies.
- The mod forwards events to the runtime and picks no model or effort itself.
- Every hook fails open: a bug may cost routing, never a tool call, a spawn or a session.
- A claim about Claude Code or a model cites the newest official docs (`code.claude.com/docs`, `platform.claude.com/docs`) or the installed binary. Behaviour with no primary source is marked as observed, with the Claude Code version it was seen on.
- `tools/docs_assets.py` writes the diagrams, badges, page heads, the docs index cards and the site's FAQ, so hand edits there are lost. Change the script or `PAGES` in `tools/docs_pages.py` (a new docs page needs a row), run it, and keep `--check` passing.
- A code comment gives a reason the code can't show, in one line, with no ticket number and no history.

## Commits

- One problem per commit. The subject is a plain sentence naming the change, with no type prefix. The body says what was wrong, why, and what the change does about it.
- No AI or tool attribution in commits, pull requests or files.
- Never force-push `main`.

Contributions are licensed under Apache-2.0, as the project is (section 5 of the [license](LICENSE)), with no CLA or sign-off. [docs/RELEASING.md](docs/RELEASING.md) has the release steps.
