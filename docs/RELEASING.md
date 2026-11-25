<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="../CONTRIBUTING.md">Contributing</a></p>
<!-- End of page head. -->

# Releasing

Work the steps in order, and tick each one only once it is checked.

1. Set the version in `plugins/model-router/.claude-plugin/plugin.json`, the one manifest that holds it (`.claude-plugin/marketplace.json` has none). Installs keep their cached copy until the version changes, so never reuse one. CI's `version` job (`tools/plugin_version.py`) fails a pull request whose version a `v*` tag holds or `main` has carried since its first tag.
2. Add a section headed `## <version> - <date>` to `CHANGELOG.md`, dated the release day, saying what the release is. `tools/docs_assets.py` stops when the plugin's version has no such section.
3. Run `python3 tools/docs_assets.py`. It writes the version into the version badge, its alt text in `README.md` and `site/index.html` and the site's `softwareVersion`, the changelog date into the site, and the `engines` floor into the Claude Code badge, both READMEs, `docs/FAQ.md`, `docs/GUIDE.md` and `site/llms.txt`.
4. Update the prose that goes stale: the options in `README.md` and `docs/GUIDE.md`, `docs/ARCHITECTURE.md`, the skill files, `docs/ROADMAP.md`, and every line the script doesn't write that names the old version or floor (`git grep -nF <old>` finds them, the changelog and test fixtures aside). When the hooks, the launcher or the stored state changed, remeasure with `python3 bench/overhead.py` (local and free) and update `docs/PERFORMANCE.md`, and `docs/SECURITY.md` for stored state. When `engines` changes, the first Claude Code version in the `validate` matrix of `ci.yml` must match it (`tests/test_ci.py` checks this).
5. Run the gates in [TESTING.md](TESTING.md) locally and fill in its latest results. If routing changed since the last release, run the live evals too, or start CI on the release branch with `gh workflow run ci.yml --ref <branch>`, which runs the `eval` job.
6. Open the release pull request, naming each issue it closes with its own keyword (`Closes #1, closes #2`). When its CI is green, merge it into `main`, the branch installs read the marketplace from.
7. Check CI on the merge commit with `gh run list --commit <sha>`. A push to `main` that changes `site/`, `docs/assets/` or `pages.yml` also deploys the site. Check that with `gh run list --workflow pages.yml --limit 1`.
8. Tag the merge commit and push the tag: `git tag -a v<version> <sha> -m "Model Router <version>"`, then `git push origin v<version>`. No workflow runs on a tag.
9. Save the changelog section to a file and create the release: `gh release create v<version> --verify-tag --title "Model Router <version>" --notes-file <file>`.
10. Check with `gh release view v<version>` that the release exists, isn't a draft and carries the notes, and with `gh issue view <number>` that each issue closed.
