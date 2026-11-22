# Releasing

Work the list in order, and tick each item only once it is checked.

1. The version is in one place: `plugins/model-router/.claude-plugin/plugin.json`. The marketplace file has no version.
2. `CHANGELOG.md` has a section headed `## <version> - <date>` for this exact version, dated the release day, describing what ships.
3. Prose that goes stale is current: README options table, requirements and minimum Claude Code version, `docs/ARCHITECTURE.md`, skill files, and the numbers they quote.
4. Locally: `python3 -m unittest discover -s tests`, `node --experimental-strip-types --test "tests/mod/*.test.ts"`, `claude plugin validate --strict .` and `claude plugin validate --strict plugins/model-router` all pass.
5. The release PR's CI is green, then the PR is merged into `main`. Installs read the marketplace from `main`.
6. CI is green on the merge commit in `main` (CI runs on every push to `main`).
7. Tag that commit `v<version>` and push the tag.
8. Create the GitHub release for the tag, not a draft, with the changelog section as its notes: `gh release create v<version> --notes-file <file> --title "Model Router <version>"`.
9. Confirm the release exists (`gh release view v<version>`) and close what it fixed, one closing keyword per issue.
