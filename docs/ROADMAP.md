<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="RELEASE_READINESS.md">Release readiness</a> &nbsp;&middot;&nbsp; Next: <a href="../CHANGELOG.md">Changelog</a></p>
<!-- End of page head. -->

# Roadmap

What to build next, highest user value first. It starts from what this release leaves undone and adds the gaps in [RELEASE_READINESS.md](RELEASE_READINESS.md). Row IDs point into [TRACEABILITY.md](TRACEABILITY.md). Move an item to "Done" when its rows pass.

## Next

1. Close the cost gap the benchmark shows. The ladder starts cheap where quality allows (standard Sonnet `medium`, complex Opus `high`, hardest Opus `xhigh`), and the verify gate, off in that measurement, is on by default. Measured on the routing code at `780ee86` at 5 or 10 runs per cell ([BENCHMARKS.md](BENCHMARKS.md)): on an Opus 5.5 session it costs less per solved task than `defaults`, `opus`, `opus-high` and the advisor in every class with 10 runs but design, where it is level, and less than Fable in all of them, but `opusplan` and every subagent on Haiku at `high` effort cost less in every class, since they solve nearly every task in this set. On a Sonnet session no row is PASS. This task set saturates <!-- bench:opus.saturated -->5<!-- /bench --> of <!-- bench:opus.classes -->8<!-- /bench --> Opus classes, so it doesn't show what the Opus margin on complex subtasks buys. Next: run the harder tier added in `3b58c62`, where Haiku should miss, against `haiku`, `haiku-high`, `opusplan` and `defaults` on both sessions, and measure the `fast` priority. Also keep each spawn's brief and signals in the bench rows. Then the baselines still missing (every subagent on Sonnet on an Opus session; Haiku, Opus and Fable on a Sonnet session), fan-outs, cache temperature and router overhead. Rows S4.12, S4.13, S4.15, S14.1.
2. Run live beyond the macOS terminal: Linux and Windows installs; Desktop, VS Code, JetBrains and the Agent SDK; Bedrock, Vertex and Foundry; an API-key login; `allowManagedHooksOnly`; split-pane teammates. Record each in "Verified live". Rows S3.7 to S3.10, S3.13, S6.1.
3. Run the five routing evals live. They changed when the session note started naming a model and effort for each kind of work, and two are new. The earlier three passed with the plugin and failed without it ([TEST_RESULTS.md](TEST_RESULTS.md)). Then add a case for the `route` skill, whose own command needs a narrow `--allow-tools` Bash grant to run inside an eval. Rows S4.11, S9.4.
4. Close the remaining edge cases: a short or unlisted-language brief, and another plugin's mod moving a spawn to another family, which the mod path counts as a substitution (check live whether `AgentSpawnResult.model` tells that from an allowlist substitution). Rows S12.10, S12.14.

## Waiting on a decision

- Whether the fallback's one denial, which protects the cap, stays as an exception to "never blocks a spawn". Row S2.25.
- Whether the Mythos models, priced but not routed, become a routable family. Row S2.15.
- A live marketplace install with one routed call, which needs the maintainer's login or an API key in a scratch config folder. Row S6.1.

## Done

- The ladder starts cheap where quality allows, from the benchmark's cost gap and Anthropic's per-task cost figures. Rows S4.2, S10.11.
- Live on Claude Code 2.1.330: the full set (acceptEdits and bypassPermissions included, subscription login recorded), teammates, forked skills, plan mode, `disableAllHooks`, a background agent's `SubagentHandback`, and cache sharing across fan-outs. Rows S2.1, S3.2, S3.5, S3.8, S3.9 (partial), S4.5, S9.6, S10.1.
- On 2.1.331: live under ultracode, workflow agents never run below the session's effort on the session's own model, and `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` pins a workflow agent so the router sets only effort. Unit-tested: a run cut off by an API error is retried and not escalated, and the checks before a turn ends wait for a running workflow or subagent. The plugin evals were rerun (3 of 3 cases pass with the plugin, none without) and the upstream baseline refreshed. Rows S4.11, S9.4, S12.32 to S12.35.
- CI ran the actions pinned to commit SHAs, the mod's tests on Linux, macOS and Windows, and the type-check of the mod and its test. Rows S6.12, S9.5.
- The state documents (this file, `TRACEABILITY.md` with its count script, `RELEASE_READINESS.md`, `TEST_RESULTS.md`, the `docs/README.md` index, `CONTRIBUTING.md`), [PERFORMANCE.md](PERFORMANCE.md) for hook latency and state size, and [SECURITY.md](SECURITY.md). Rows S4.10, S10.12, S11.7, S11.9.
- Shared state files carry a version; one that fails to parse is set aside, and one from a newer release is read but never written. Per-session files carry none. The registry and overrides carry a `schema` version. A clock moved backwards no longer reads an old call as a redo. Rows S6.6, S6.7, S6.15, S12.20, S12.21, S12.27.
- `models --reset` says what it cleared; `--json` for `usage` and `models`, with versioned schemas. Rows S6.14, S12.28.
- Each subagent's dollars are Claude Code's own estimate, fast mode, long prompts and search fees included, labeled as an estimate, with a note on a subscription; the main conversation's cost is left to `/usage`. Rows S2.6, S4.6 to S4.8, S10.7.
- A benchmark harness, then eight task classes, 30 tasks in all, the `sonnet-medium`, `sonnet-high`, `fable`, `opusplan` and advisor baselines, and security reviews graded from a `FINDING.json`. The routing evals' expected routes are checked in the unit gate. Rows S4.1, S4.14, S10.11, S11.8, S11.12, S14.1 to S14.4.
- SVG diagrams in the README, the changelog's Python versions true to the CI matrix, a dead-code recheck, and the `agent.spawn` research re-cited to the mod API types. Rows S5.5, S5.6 (partial), S8.2, S8.4, S10.13, S13.6.
- Profile fixes: briefs in other languages start at standard, a negated limit ends with its clause, teammates' reports no longer raise the lead's effort, a hard skill typed as a slash command raises that turn, and 268 labeled Agent briefs check the profile. Rows S2.14, S3.4, S9.8, S12.10 (partial), S12.12, S12.13.
- Skills pre-approve only their own commands, the upstream check has a deadline per request, the Stop check counts test runs inside subagents, and the newest model ID is used on providers whose aliases run older models. Rows S2.23, S3.10 (partial), S10.6, S12.29.
