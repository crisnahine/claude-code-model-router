# Roadmap

What to build next, highest user value first. It starts from what 0.1.0 left undone and adds the gaps in [RELEASE_READINESS.md](RELEASE_READINESS.md). Row IDs point into [TRACEABILITY.md](TRACEABILITY.md). Move an item to "Done since 0.1.0" when its rows pass.

## Next

1. Close the cost gap the benchmark shows. At the same pass rate the router cost 1.2 to 2.2 times the best baseline in 7 of 8 classes; it started 1.4 agents per run against 1.0, a verifier in 28 of 88 runs, and spent 29% of its cost on Opus. The ladder is the Architect's call from [BENCHMARKS.md](BENCHMARKS.md); rerun the same suite after any change. Then run the baselines still missing (`haiku`, `opus`, `fable`, `opusplan`, the advisor), fan-outs, cache temperature and router overhead. Rows S4.12, S4.13, S4.15, S14.1.
2. Run live beyond the macOS terminal. Linux and Windows installs; Desktop, VS Code, JetBrains and the Agent SDK; Bedrock, Vertex and Foundry; an API-key login; `allowManagedHooksOnly`; split-pane teammates. Record each in "Verified live". Rows S3.7 to S3.10, S3.13, S6.1.
3. Decide how fast mode shows in the spend log. Claude Code doesn't say which requests ran fast, so the only full figure is its own estimate in `/cost`, which a mod can read. Rows S4.8, S10.7.
4. Add a `claude plugin eval` case for the `route` skill. The three routing cases pass with the plugin and fail without it ([TEST_RESULTS.md](TEST_RESULTS.md)), but the skill's own command needs a narrow `--allow-tools` Bash grant to run inside an eval. Rows S4.11, S9.4.
5. Confirm the CI changes in CI. Actions are pinned to commit SHAs, the mod's tests run on every OS, and the mod and its test are type-checked; none of it has run in CI yet. Rows S6.12, S9.5.
6. Close the remaining edge cases: a short or unlisted-language brief, and another plugin's mod moving a spawn to another family, which the mod path counts as a substitution (check live whether `AgentSpawnResult.model` tells that from an allowlist substitution). Rows S12.10, S12.14.

## Waiting on a decision

- Whether a project's file may raise levels and effort (the open question on project raises). The user decides; 0.1.0 allows raises. Row S6.4.
- Whether the fallback's one denial, which protects the cap, stays as an exception to "never blocks a spawn". Row S2.25.
- Whether the standard level runs Sonnet at `high`, as shipped, or at `medium`. In the benchmark `medium` solved as often for less: significantly on implementation, not significantly on known-cause bugfixes. Row S10.11.
- Whether the Mythos models, priced but not routed, become a routable family. Row S2.15.
- A live marketplace install with one routed call, which needs the maintainer's login or an API key in a scratch config folder. Row S6.1.

## Done since 0.1.0

- The state documents: this file, `TRACEABILITY.md` with its count script, `RELEASE_READINESS.md`, `TEST_RESULTS.md`, the `docs/README.md` index and `CONTRIBUTING.md`.
- State files carry a version and migrate; one that fails to parse is set aside, and one from a newer release is read but never written. Rows S6.6, S6.7, S12.21.
- A security assessment in [SECURITY.md](SECURITY.md). Rows S10.12, S11.7.
- `models --reset` says what it cleared and leaves files it can't safely touch. Row S12.28.
- Each request is priced on its own, with Haiku's rate for prompts over 100K tokens. Rows S2.6, S4.7.
- `--json` for `usage` and `models`, with versioned schemas. Rows S6.14, S12.28.
- Teammates verified live on 2.1.330. Rows S2.1, S3.2, S10.1.
- Cache sharing across fan-outs measured live: copies of one brief started together share the first copy's prefix, and a copy on another model shares nothing. Row S4.5.
- The full live set rerun on 2.1.330, acceptEdits and bypassPermissions included, and the subscription login recorded. Rows S3.8, S3.9, S9.6.
- The routing evals' expected routes are checked in the unit gate, on every CI cell. Row S14.4.
- A benchmark harness and a 336-run comparison of the router, `defaults`, `sonnet-medium` and `sonnet-high` on all sixteen tasks, with saved sessions, a plugins root per run, and pauses on rate or usage limits. Rows S4.1, S4.14, S10.11, S11.8, S11.12, S14.3.
- SVG diagrams in the README, and the 0.1.0 changelog's Python versions corrected. Rows S10.13, S8.2.
- A dead-code recheck, the dogfood ledger read, every outside source linked, and the `agent.spawn` research re-cited to the mod API types. Rows S5.5, S8.4, S13.6, S5.6 (partial).
- Briefs in other languages start at standard; a negated limit ends with its clause; teammates' reports no longer raise the lead's effort. Rows S12.10, S12.12, S12.13.
- A `schema` version on the registry and overrides, and a written compatibility policy. Row S6.15.
- Inline and forked skills, plan mode and `disableAllHooks` checked live; forked skills stay on the session's model. Rows S3.5, S3.8, S3.13 (partial).
- Skills pre-approve only their own commands; the daily fetch has a deadline; the Stop check counts test runs inside subagents. Rows S12.29, S10.6, S2.23.
- Hook latency and state size measured in [PERFORMANCE.md](PERFORMANCE.md); each hook now starts Python once and skips the network modules. Rows S4.10, S11.9, S12.27.
- A clock moved backwards no longer reads an old call as a redo or stretches an outage, and only the three newest set-aside copies of a state file are kept. Rows S12.20, S12.27.
- A hard skill typed as a slash command raises that turn, and a skill that takes its arguments is judged by them. Row S3.4.
- A labeled corpus of 219 Agent briefs checks the profile, which now counts judgment verbs, named systems and more risk words, and ends a limit at and, or or then before a task. Rows S2.14, S9.8, S12.12.
- The newest model ID on providers whose aliases run older models, falling back to the alias. Row S3.10.
- A failed daily fetch waits an hour, doubling to a day; the Mythos models are priced; each benchmark run gets a plugins root of its own; the glossary covers every named module. Rows S10.6, S2.15, S14.2, S5.4.
- A background agent's `SubagentHandback` in auto mode verified live. Rows S3.8, S9.6.
- The benchmark covers eight task classes, two tasks each, and the `sonnet-medium`, `sonnet-high`, `fable`, `opusplan` and advisor baselines; security reviews are graded from a `FINDING.json`. Rows S14.1, S14.2.
