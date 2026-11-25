# Contributing

## Commits

Commit subjects follow Conventional Commits: `type(scope): summary (#PR)`. The scope is optional. Types we use: `feat`, `fix`, `perf`, `deprecate`, `remove`, `refactor`, `test`, `docs`, `ci`, `chore`, `build`.

## Releases

Each release gets a section in CHANGELOG.md, written for customers, not for us.

- The section heading is `## [x.y.z] - YYYY-MM-DD`, with the release date, directly under `## [Unreleased]`.
- Group the lines under these headings, in this order, and leave out a heading with nothing under it: Added (`feat`), Changed (`perf`), Deprecated (`deprecate`), Removed (`remove`), Fixed (`fix`).
- One line per change, ending with its PR number in parentheses, for example `(#123)`.
- `refactor`, `test`, `docs`, `ci`, `chore` and `build` commits don't go in the changelog.
- Before you tag, run `python3 tools/check_changelog.py <version>`.
