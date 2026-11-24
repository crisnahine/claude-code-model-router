# How the plugin is tested

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.3.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

## What we built

- `tests/` holds about 730 Python `unittest` tests: `test_model_router.py` (541), `test_bench.py` (148), `test_docs_assets.py` (30), `test_routing_corpus.py` (8), `test_traceability.py` (3). CI runs them on Linux, macOS and Windows under Python 3.9 and 3.13 (`.github/workflows/ci.yml`, job `test`).
- Hook tests build hook stdin by hand and call `hooks.post_agent`, `spawn.decide` and the rest directly. `Launcher.test_launcher_runs_hooks_and_commands` pipes JSON into `scripts/model-router.sh pre-agent`, the way a command hook runs.
- `test_routing_corpus.py` runs `profile.profile_call` and `profile.profile_prompt` over three labelled files in `tests/fixtures/`: `routing-corpus.json` (268 briefs with `lowest`/`highest`), `request-corpus.json` (116 prompts with an exact `level` and `signals`) and `agent-name-corpus.json`. `OVER_ROUTE_BUDGET = 0.02` and the `known_under` marks are set to today's results.
- `tests/mod/router.test.ts` tests `hooks/router.ts` under `node --test` with a hand-built `on` and `$` (the `load()` helper fakes `$.process.run`, `$.settings.read`, `$.session.*`, `$.clock.after`). `tests/fixtures/mod-payloads.json` pins what the mod sends the Python CLI, and `ModPayloads` checks the Python side reads the same file.
- `tests/mod/typecheck.sh` type-checks `router.ts` and its test with `tsc` against `mods/types/claude-code.d.ts` downloaded from the `anthropics/claude-code` GitHub repo at a pinned commit (`1006c2e`), with `types: ["node"]`. It does not use the files Claude Code writes under `.claude-plugin/types/`.
- `ModApiTypes` in `test_model_router.py` checks the `e.<field>` names in `router.ts` against a types file it finds under a temp `bundled-skills/<version>/*/plugin-authoring/types/` folder. In CI no `claude` is installed in the `test` job, so it skips.
- CI job `validate` runs `claude plugin validate --strict` on the marketplace and the plugin under Claude Code 2.1.329 and 2.1.330. Job `eval` runs `claude plugin eval plugins/model-router` on 2.1.330 only when started by hand. The three cases in `plugins/model-router/evals/` grade the trace with a regex on `resolvedModel`.
- `upstream-watch.yml` reads the changelog and docs index daily and opens an issue for a person to read.

## What the sources say

Testing a mod

- The documented runner is `claude plugin test` with the `claude-code/testing` kit. "A test loads your mod, sends events through its hooks the way Claude Code would, and checks what the hooks did, without a session, a sign-in, or a network. You run tests from your shell with `claude plugin test`, and each test file imports the test kit, a test library in the `claude-code/testing` module." https://code.claude.com/docs/en/plugins/mods/test
- The kit's `$` is the engine's own. "`$`: the test's own `$`, which acts as Claude Code ... Each of its methods fires the event of the same name, sends it through your mod's hooks, and resolves to the result." Stubs go through `on`: "`$.process.run` | `($, e) => ({ value: { exitCode: 0, stdout: '...', stderr: '' } })`." https://code.claude.com/docs/en/plugins/mods/test
- The generated types say the same: the kit runs each test "in an environment like the one a plugin's hooks run in (no fs, network or process), the plugin loaded from the folder by the engine's own host". Source: generated type file `plugins/model-router/.claude-plugin/types/claude-code/index.d.ts` (header "Written by Claude Code 2.1.330"), lines 34 to 38.
- Where the tests go: "Give each test file a name that ends in `.test.ts` ... and save it anywhere in the plugin directory." "The command exits with status 1 when a test fails, so it works in CI." https://code.claude.com/docs/en/plugins/mods/test. The CLI runs "every file under it whose name ends in `.test.ts` or `.test.tsx`". https://code.claude.com/docs/en/plugins/cli-reference (section "plugin test")
- A hooks module runs with no Node: "A hooks module runs in an environment of its own: no DOM, no Node." The fitting tsconfig names `"lib": ["es2023"], "types": []` and includes `"tests"`. Source: generated type file, lines 23 to 24 and 75 to 93.
- Kit behavior changes between releases, for example: "Changed `claude plugin test`: a failed `expect` inside a hook the test registered, or a stub answer the engine refuses, now fails the test instead of passing silently" (2.1.292). https://code.claude.com/docs/en/changelog

Type files and validate

- "When Claude Code loads a mod from `--plugin-dir` in an interactive session ... it writes TypeScript declaration files into the mod's `.claude-plugin/types/` directory. They describe the exact events, mods API methods, and elements in the Claude Code version you're running." "The events and methods can change between releases, so trust these files over any page, this one included, when they disagree." https://code.claude.com/docs/en/plugins/mods/create (section "Get type definitions for your version")
- The type file itself: "EARLY ACCESS: this surface may change between releases without notice." Source: generated type file, line 4.
- `claude plugin validate` "checks the manifest and runs the same static analysis on the hooks module's source that Claude Code runs when it loads a mod". A misspelled event is "an error such as `"tool.calls" is not an event`". https://code.claude.com/docs/en/plugins/mods/create (section "Check what Claude Code reads from your mod")
- Since 2.1.323, validate also type-checks the module: "Added type checking of a plugin's TypeScript hooks module to `claude plugin validate`; each finding is a warning, and `--strict` fails on it." https://code.claude.com/docs/en/changelog (2.1.323)
- An unknown event name fails the whole load: "A name this build does not know, or a glob that selects no event, refuses the whole module." Source: generated type file, `On` type.
- `--strict` in CI: "keep `--strict`, which also fails the run with exit code 1 on warnings". https://code.claude.com/docs/en/plugins/publish

Classic (command) hooks

- The hook payloads are typed in the generated file too: "What a classic hook receives on stdin for each event, by event name: the Agent SDK's `<Event>HookInput`." Source: generated type file, `ClassicHookInputs`. The Agent SDK reference lists them (`PreToolUseHookInput`, `SubagentStopHookInput`, `PostModelSwitchHookInput` and the rest). https://code.claude.com/docs/en/agent-sdk/typescript
- The documented offline test for a command hook is piping sample JSON: "Test it manually by piping sample JSON: `echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | ./my-hook.sh`". https://code.claude.com/docs/en/hooks-guide (section "Limitations and troubleshooting")
- The documented live check is the debug log: "Start Claude Code with `claude --debug-file <path>` ... In Claude Code v2.1.296 or later, each command hook on a tool event, `UserPromptSubmit`, `SessionStart`, `Stop`, and several other events leaves one [`Hooks:` line] when it finishes". https://code.claude.com/docs/en/hooks (section "Debug hooks"). For a mod, "A `claude -p` run with `--plugin-dir`: stderr, in the default text output format." https://code.claude.com/docs/en/plugins/mods/troubleshoot
- Mods run in `claude -p`: "`claude -p` and the Agent SDK | Yes | No" (hooks run, nothing drawn). https://code.claude.com/docs/en/plugins/mods/overview (section "Where mods run")

Evals

- `claude plugin eval` is the documented live test: "For each run of a case, Claude Code starts a fresh, isolated non-interactive session with only your plugin loaded, sends the prompt ... Each grader then checks the final reply, the transcript, or a file". Uses include "Catch regressions when you change the plugin or a new model is released". https://code.claude.com/docs/en/plugin-evals
- Grader advice: "Give each case one grader on the result, such as the final message or a produced file, and one on the steps Claude took to produce it, such as `tool_used` or `tool_order`. Together they tell you both whether the answer was right and whether your plugin produced it." https://code.claude.com/docs/en/plugin-evals
- CI advice: "pin both models so scores are comparable over time"; for `--model`, "Pin it in CI so a model rollout isn't mistaken for a plugin regression". https://code.claude.com/docs/en/plugin-evals
- The field our graders match is documented: "`resolvedModel` | string | ... | Model the subagent started on, which may differ from the requested model". https://code.claude.com/docs/en/hooks (Agent tool response)
- "A plugin that loads without errors can still fail to steer Claude the way you intend. `claude plugin eval` ... runs your test cases with and without the plugin and scores the difference." https://code.claude.com/docs/en/plugins/create (section "Check that the plugin changes Claude's behavior")

Labelled test sets

- Anthropic's eval guide: "Be task-specific: Design evals that mirror your real-world task distribution. Don't forget to factor in edge cases!" and success criteria are measured "on a held-out test set". https://platform.claude.com/docs/en/test-and-evaluate/develop-tests

What the repo shows against those sources

- The pinned GitHub types file is not the build's file. It has no "Written by Claude Code" line, and a diff against the 2.1.330 generated file (first line removed) is 748 lines. `typecheck.sh` adds `types: ["node"]` and `lib: ["ES2022"]`, so Node globals in `router.ts` would pass it, though the engine has no Node.
- `docs/CLAUDE_CODE_DOCS.md` row S9.5 says "The mod and its test are type-checked against those types" (the generated ones). They are not; they are checked against the pinned GitHub file.
- The routing corpus was tuned with the regex. 20 of the 23 commits that touch `tests/fixtures/routing-corpus.json` also touch `profile.py` or `config.py`, and 6 of them rewrite or drop label lines. `RequestCorpus.test_every_request_keeps_its_level_and_reasons` asserts the exact `(level, signals)` the profiler gives today, so it is a snapshot, not a truth set.
- The corpora are software work. Of 268 briefs, the only ones with non-software words still name code (invoices tables, email columns). The request corpus has one non-software prompt ("write a short poem about the build").
- The redo eval `route-redo-escalates` covers only an identical repeat. The marked redo (`description` starting `Redo:`, `calls.marked_redo`), which commit `a2f126c` made the main redo path, has no live case. No eval grades the answer itself, only the model that ran.

Which tests assert real behavior, and which only assert the regex

- Real behavior against a documented contract: `Launcher` (pipes JSON into the launcher, as the hooks guide says), `Hooks` and `Gates` output shapes (`hookSpecificOutput`), `Allowlist` and settings precedence, `JsonOutputs` and `subagent-handback.jsonl` (recorded from real runs), `ModPayloads` (the contract between our two halves), and the three live evals. `ProfileProperties` asserts invariants (a signal word never lowers a level), which holds whatever the labels say.
- Real logic on made-up input: `Router`, `RepeatGuard`, `Calls`, `Fallback`, `SpawnPaths` and the Node mod tests. They test our code, but every payload is hand-built, so they cannot see a field Claude Code renamed.
- The regex against a corpus tuned with it: `RoutingCorpus`, `RequestCorpus`, `AgentNameCorpus`, most of `Profile` (33 example briefs), and `RoutingEvals` (checks each eval's `expected_outcome`, which was written to match the router). These catch an unplanned change in the regex. They say nothing about whether a level is right.
- Text checks: `DocsAssets`, `Shipped`, `ReadmeDiagrams`, `DesignDecisions`, `Traceability`.

What catches a release that renames a field we read

- An event name in `router.ts`: `claude plugin validate --strict` in the `validate` job, but only on 2.1.329 and 2.1.330. Nothing runs on a newer release.
- A field on `e` in `router.ts`: the validate type check (2.1.323 and later) on those two versions. `typecheck.sh` reads a pinned file that never moves, and `ModApiTypes` skips in CI.
- A field the Python hooks read from stdin (`to_model`, `background_tasks`, `is_interrupt`, `tool_response.resolvedModel`, `effort` and the rest): nothing. No test compares them to `<Event>HookInput`.
- End to end: the evals would show it as a wrong `resolvedModel`, but they run only by hand and only on 2.1.330.
- A person reading the daily upstream-watch issue.

## Verdict

Partly right. The plugin uses each documented check: `claude plugin validate --strict` in CI, `claude plugin eval` with graders on a documented field, and piped JSON for command hooks. The Python logic tests are real and run on three systems. But the mod tests skip the documented `claude plugin test` kit and use a hand-built fake of the engine. The type check reads a pinned file the docs never name, where they say to "trust these files" Claude Code writes for the build. No check runs on a release newer than 2.1.330, and nothing checks the Python hooks' stdin fields. The corpus tests check the regex against labels tuned alongside it, from software work only, with no held-out set, where Anthropic's eval guide asks for a held-out set that mirrors the real task mix.

## What to change

1. Move the mod tests to `claude plugin test`. Rewrite `tests/mod/router.test.ts` as `plugins/model-router/tests/router.test.ts` on `claude-code/testing`: fire `$.agent.spawn`, `$.turn.step`, `$.prompt.submit` and the rest through the engine's host, and stub `process.run`, `process.hookEnv`, `settings.read`, `session.*` and `clock` with `on`. Delete the `load()` fake and the `tests/mod/` folder, and change the CI `mod` job to install Claude Code and run `claude plugin test plugins/model-router`. Backed by https://code.claude.com/docs/en/plugins/mods/test. Judgment: the docs give this as the way to test a mod, but nothing makes it mandatory.
2. Delete `tests/mod/typecheck.sh` and the CI `typecheck` job, and rely on the validate type check, which reads the types of the build it runs on. This deletes the pinned `TYPES_COMMIT` and the `types: ["node"]` config that hides Node globals from the check. Backed by https://code.claude.com/docs/en/changelog (2.1.323) and https://code.claude.com/docs/en/plugins/mods/create. Judgment.
3. Fix `docs/CLAUDE_CODE_DOCS.md` row S9.5 and `TRACEABILITY.md` S9.5 to name what checks the types: `claude plugin validate --strict` per Claude Code version in the matrix (after item 2). Today the row claims a check against the generated types that does not happen. Backed by https://code.claude.com/docs/en/plugins/mods/create. Required: our docs page cites that rule and the code does not follow it.
4. Add `latest` to the `validate` matrix (and to the `mod` job after item 1), and run that job on the daily schedule `upstream-watch.yml` already uses. Then a new release that renames an event or a field fails CI on the day it ships, without waiting for a person to read the upstream issue. Backed by the generated type file ("this surface may change between releases without notice") and https://code.claude.com/docs/en/plugins/mods/create ("The events and methods can change between releases"). Judgment.
5. Check the Python hooks' stdin fields against the declared hook inputs. Replace `ModApiTypes` (delete its `bundled-skills` temp-folder lookup, an undocumented path) with a test that lists, per classic event in `hooks/hooks.json`, the fields `modelrouter/hooks.py`, `spawn.py`, `gates.py` and `session.py` read, and asserts each is in that event's `<Event>HookInput`. Run it in the `validate` matrix job against the types for that version. Backed by the generated type file (`ClassicHookInputs`, "the Agent SDK's `<Event>HookInput`") and https://code.claude.com/docs/en/agent-sdk/typescript. Judgment. How CI gets the types for a given version is not documented; see below.
6. Split the routing corpus. Freeze a held-out set, labelled before the profiler runs on it and never changed in a commit that touches `profile.py`. Report its pass rate in `test_routing_corpus.py` and keep the current files as the regression set. Turn `RequestCorpus` exact-signal asserts into a level range like `RoutingCorpus`, so the snapshot stops passing as truth. Backed by https://platform.claude.com/docs/en/test-and-evaluate/develop-tests ("held-out test set"). Judgment.
7. Add non-software briefs to both the held-out set and the regression set: writing, research, data, planning, support replies, with the same `lowest`/`highest` rules. The plugin must route any kind of work, and today the corpora are software work. Backed by https://platform.claude.com/docs/en/test-and-evaluate/develop-tests ("mirror your real-world task distribution"). Judgment.
8. Add live eval cases for what changed most: a marked redo (description `Redo: list files` after a first run) and one non-software brief (for example a short research or writing task that should run above `lookup`). Each case also gets one grader on the answer, such as a `regex` on `last_message` for the review case naming the `or` bypass, beside the existing `resolvedModel` grader. Backed by https://code.claude.com/docs/en/plugin-evals ("one grader on the result ... and one on the steps"). Judgment.
9. Run the `eval` job on a weekly schedule as well as by hand, on the newest Claude Code, keeping `--max-cost-usd 2` and `--no-publish`. It is the only check that sees a field rename end to end. Backed by https://code.claude.com/docs/en/plugin-evals ("Catch regressions when you change the plugin or a new model is released", section "Run evals in CI"). Judgment, since it spends API money.

## What the sources do not say

- Whether `claude plugin validate` type-checks `*.test.ts` files, or only the hooks module. Not documented.
- How to get the `.claude-plugin/types/` files for a given version in CI. The docs say they are written when a mod loads from `--plugin-dir` "in an interactive session". Not documented for `claude -p`, `claude plugin test` or `claude plugin validate`. The `anthropics/claude-code` repo has `mods/types/claude-code.d.ts` at the tags `v2.1.329`, `v2.1.330` and `v2.1.331` (checked over HTTP), but no docs page names that file or promises it matches a release.
- Which Agent SDK version matches which Claude Code version, for item 5. Not documented.
- Whether `claude plugin test` runs on a fresh CI runner that never signed in. The docs say it needs no sign-in, but also that it can print "hooks modules are turned off in this process: the rollout switch served off". Whether a runner gets that switch on is not documented.
- Whether `claude plugin test` runs a plugin's settings hooks from `hooks.json` (our Python side). The type file says of `$.classic`: "No settings hook runs beneath". So it does not seem to, and the Python side still needs its own tests.
- How a routing classifier's labels should be set or audited. Anthropic's eval guide covers held-out sets in general. Nothing is documented for a plugin that picks models.
- Whether a mod event's `e` fields are kept stable across releases. The type file says only that the surface "may change between releases without notice".
- This review did not run the test suite, validate, the evals or `claude plugin test`, so it does not say whether today's CI passes the validate type check.
