<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="../CONTRIBUTING.md">Contributing</a></p>
<!-- End of page head. -->

# Releasing

Work the steps in order, and tick each one only once it is checked.

1. Set the version in `plugins/model-router/.claude-plugin/plugin.json`, the one place it lives (`.claude-plugin/marketplace.json` has none). Installs keep their cached copy until the version changes, so never reuse one. CI's `version` job (`tools/plugin_version.py`) fails a pull request whose version a `v*` tag holds or `main` has carried since its first tag.
2. Add a section headed `## <version> - <date>` to `CHANGELOG.md`, dated the release day, saying what the release is. `tools/docs_assets.py` stops when the plugin's version has no such section.
3. Run `python3 tools/docs_assets.py`. It writes the version into the version badge and its alt text in `README.md` and `site/index.html`, the changelog date into the site, and the `engines` floor into the Claude Code badge and the pages that state it.
4. Update the prose that goes stale: the options in `README.md` and `docs/GUIDE.md`, `docs/ARCHITECTURE.md`, the skill files, `docs/ROADMAP.md` and the latest results in `docs/TESTING.md`. When the hooks, the launcher or the stored state changed, remeasure with `python3 bench/overhead.py` (local and free) and update `docs/PERFORMANCE.md` and `docs/SECURITY.md`. When `engines` changes, the first Claude Code version in the `validate` matrix of `ci.yml` must match it (`tests/test_ci.py` checks this).
5. Run the gates in [TESTING.md](TESTING.md) locally. If routing changed since the last release, run the live evals too, or start CI's `eval` job with `gh workflow run ci.yml`.
6. Open the release pull request, naming each issue it closes with its own keyword (`Closes #1, closes #2`). When its CI is green, merge it into `main`, the branch installs read the marketplace from.
7. Check CI on the merge commit with `gh run list --commit <sha>`. A push to `main` that changes `site/`, `docs/assets/` or `pages.yml` also deploys the site. Check that with `gh run list --workflow pages.yml --limit 1`.
8. Tag the merge commit `v<version>` and push the tag. No workflow runs on a tag.
9. Save the changelog section to a file and create the release: `gh release create v<version> --verify-tag --title "Model Router <version>" --notes-file <file>`.
10. Check with `gh release view v<version>` that the release exists, isn't a draft and carries the notes, and that the issues closed.
