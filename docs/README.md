<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; Next: <a href="GUIDE.md">Guide</a></p>
<!-- End of page head. -->

# Model Router documentation

Every page about Model Router, the Claude Code plugin that keeps the Claude model and effort Claude names for each agent within your cap, fills in a missing effort and runs a marked redo higher. New here? Start with the [README](../README.md) and the [questions](FAQ.md).

<!-- Cards, written by tools/docs_assets.py from PAGES in tools/docs_pages.py. -->
<table>
<tr><th colspan="2" align="left">Start here<br><sub>What it is, and the questions people ask first.</sub></th></tr>
<tr>
<td width="50%" valign="top"><a href="../README.md"><b>Model Router</b></a><br>What does it do, and how do I install it?</td>
<td width="50%" valign="top"><a href="GUIDE.md"><b>Guide</b></a><br>How do I configure it for my setup?</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="FAQ.md"><b>Questions</b></a><br>What is Model Router, and how does it pick?</td>
<td width="50%"></td>
</tr>
<tr><th colspan="2" align="left">How it works<br><sub>The design, its words and sources, and what it costs to run.</sub></th></tr>
<tr>
<td width="50%" valign="top"><a href="ARCHITECTURE.md"><b>Architecture</b></a><br>How do two paths share one router?</td>
<td width="50%" valign="top"><a href="GLOSSARY.md"><b>Glossary</b></a><br>What do call, level, pick and redo mean?</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="SECURITY.md"><b>Security</b></a><br>What reaches the network, and who can steer it?</td>
<td width="50%" valign="top"><a href="PERFORMANCE.md"><b>Performance</b></a><br>What does the router cost in time and disk?</td>
</tr>
<tr><th colspan="2" align="left">Evidence<br><sub>What it follows, and what was measured and tested.</sub></th></tr>
<tr>
<td width="50%" valign="top"><a href="CLAUDE_CODE_DOCS.md"><b>Built on the docs</b></a><br>Which Claude Code docs does it follow, and where?</td>
<td width="50%" valign="top"><a href="BENCHMARKS.md"><b>Benchmarks</b></a><br>What does a solved task cost, with and without it?</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="TESTING.md"><b>Testing</b></a><br>Which checks gate a change, and what passed last?</td>
<td width="50%"></td>
</tr>
<tr><th colspan="2" align="left">The project<br><sub>What comes next, what changed, and how to take part.</sub></th></tr>
<tr>
<td width="50%" valign="top"><a href="ROADMAP.md"><b>Roadmap</b></a><br>What gets built next, and why first?</td>
<td width="50%" valign="top"><a href="../CHANGELOG.md"><b>Changelog</b></a><br>What changed in each release?</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="../CONTRIBUTING.md"><b>Contributing</b></a><br>How does a change get merged?</td>
<td width="50%" valign="top"><a href="RELEASING.md"><b>Releasing</b></a><br>How is a release cut, step by step?</td>
</tr>
</table>
<!-- End of cards. -->

## Delivery items

Where each delivery item lives. Checked 2026-11-22.

| # | Item | Where |
| -: | :- | :- |
| 1 | Source code and architecture | [ARCHITECTURE.md](ARCHITECTURE.md), [GLOSSARY.md](GLOSSARY.md) |
| 2 | Capabilities and commands | [README.md](../README.md) "Commands", "Configuration", [GUIDE.md](GUIDE.md) |
| 3 | Supported Claude Code versions, surfaces, platforms and providers | [README.md](../README.md) "Compatibility", [GUIDE.md](GUIDE.md) "Compatibility", [ARCHITECTURE.md](ARCHITECTURE.md) "Verified live" |
| 4 | Install and usage | [README.md](../README.md) "Install the Claude Code plugin" |
| 5 | Automated test results | [TESTING.md](TESTING.md) |
| 6 | Security assessment | [SECURITY.md](SECURITY.md) |
| 7 | Reproducible cost and quality results, with raw data | [BENCHMARKS.md](BENCHMARKS.md), raw data under [bench/results/](../bench/results/) |
| 8 | Hook latency and state-size measurements | [PERFORMANCE.md](PERFORMANCE.md) |
| 9 | Roadmap | [ROADMAP.md](ROADMAP.md) "Next" |
| 10 | Comparison against the baselines | [BENCHMARKS.md](BENCHMARKS.md) |
| 11 | Release checklist and changelog | [RELEASING.md](RELEASING.md), [CHANGELOG.md](../CHANGELOG.md) |

Also kept: [CONTRIBUTING.md](../CONTRIBUTING.md) (gates, commit rules and where to work) and [compat/baseline.json](../compat/baseline.json) (the Claude Code version, docs pages, model aliases and model status last reviewed).

## Outside tools and sources

Every outside project, library, benchmark and tool these docs name, with its primary source. Checked 2026-11-22: each page loaded, or, for npm packages, `npm view` returned the version. Claude Code and Claude API claims cite code.claude.com and platform.claude.com where made.

| Name | Used for | Primary source |
| :- | :- | :- |
| SWE-Bench Pro (Scale AI) | The cost and effort figures quoted from Anthropic's cost guide | https://labs.scale.com/papers/swe_bench_pro |
| Percentile bootstrap (Efron, Annals of Statistics 7(1)) | The 95% cost and verdict intervals in [BENCHMARKS.md](BENCHMARKS.md) | https://doi.org/10.1214/aos/1176344552 |
| `@anthropic-ai/claude-code` on npm | CI's `validate` and `eval` jobs and the daily `newest` job ([SECURITY.md](SECURITY.md)) | https://github.com/anthropics/claude-code (the package's homepage in the registry) |
| TypeScript `tsc` 6.0.3 and `skipLibCheck` | The mod type check ([TESTING.md](TESTING.md)) | https://www.typescriptlang.org/tsconfig/#skipLibCheck |
| `@types/node` 22.20.5 | The mod test's type check | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/node (the package's homepage in the registry) |
| Node.js test runner and type stripping | The mod tests | https://nodejs.org/api/test.html, https://nodejs.org/api/typescript.html |
| GitHub CLI `gh run list` | Reading CI runs | https://cli.github.com/manual/gh_run_list |
| tmux | Interactive live runs ([ARCHITECTURE.md](ARCHITECTURE.md) "Verified live") | https://github.com/tmux/tmux/wiki |

