# Requirement traceability

One row per requirement the project holds itself to, numbered by the section of the maintainer's project brief it comes from (Sections 2 to 14). Checked on 2026-11-22 against `v0.1.0` (`8ec8eec`) plus the unreleased commits after it, on Claude Code 2.1.330. The CI evidence is the run on `8ec8eec`; nothing after it has run in CI yet.

Summary: 91 PASS, 51 PARTIAL, 15 FAIL, 0 BLOCKED, 157 requirements.

The line above is counted by `python3 tools/traceability.py`, never by hand. `tests/test_traceability.py` fails when it disagrees with the rows. After changing a status, run the script and paste its output over that line.

Statuses: **PASS** implemented and verified; **PARTIAL** incomplete or not verified enough; **FAIL** not implemented, or failed; **BLOCKED** a documented limit of Claude Code or a provider. A status comes only from evidence someone checked. Anything unverified is PARTIAL.

How to read the evidence column:

- `Class.test_name` is a test in `tests/test_model_router.py`. A quoted title is a test in `tests/mod/router.test.ts`.
- "CI main" is run [45004998622](https://github.com/crisnahine/claude-code-model-router/actions/runs/45004998622) on `8ec8eec`: all 8 jobs passed. Job by job in [TEST_RESULTS.md](TEST_RESULTS.md).
- "Live 2.1.330" is the "Verified live" section of [ARCHITECTURE.md](ARCHITECTURE.md): macOS only, headless and an interactive terminal in auto mode, before 0.1.0 shipped. Not rerun since.
- "In progress (m1-0N)" means another developer is changing that part in this round. The row moves when that work lands.

Paths are under `plugins/model-router/` unless they start with `tests/`, `docs/`, `.github/` or `tools/`.

## Section 2: two paths, one router

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S2.1 | `agent.spawn` routes every subagent, teammate and workflow stage with the parent's exact model | Mod forwards the spawn to `mod-spawn` with `parentModel` and kind | `hooks/router.ts`, `scripts/modelrouter/hooks.py` `mod_spawn` | Unit, Node, live | "a spawn passes the pick model to next"; `Mod.test_spawns_are_routed_from_the_parents_exact_model`; `Mod.test_workflow_stages_and_teammate_roles_without_a_file_are_routed`; live 2.1.330 for subagents and a workflow stage. No teammate spawned live | PARTIAL |
| S2.2 | Forks and resumed runs pass through untouched | `if (e.fork \|\| e.resume) return next(e)` | `hooks/router.ts` | Node | "forks and resumed runs pass through with no CLI call" | PASS |
| S2.3 | `tool.call` on `Agent` reads a caller's effort so it is never overwritten | `callerEffort` map passed to `mod-spawn` | `hooks/router.ts` | Node, unit | "an effort on the Agent call reaches mod-spawn"; `Router.test_an_agents_own_effort_and_a_callers_effort_are_kept` | PASS |
| S2.4 | `turn.step` sets each routed agent's effort on every request, a subagent's first request included, and the main loop's turn effort | `agentEffort`, `promptEffort`, `turnEffort` maps | `hooks/router.ts` | Node, live | "a routed agent steps at the pick effort"; "an agent stepping before its spawn resolves still gets the pick effort"; live 2.1.330 first request carried the routed effort | PASS |
| S2.5 | `turn.start` raises the main loop's effort for a hard request, never lowers it, skips where an effort change drops the cache | `mod-turn` picks; `turn.step` applies only a raise | `hooks/router.ts`, `scripts/modelrouter/hooks.py` `mod_turn` | Node, unit, live | "the main loop effort only raises and ends with the turn"; "a lower main loop effort is ignored"; `Mod.test_turn_effort_rises_for_hard_requests_only`; `Mod.test_turn_effort_is_left_alone_where_an_effort_change_drops_the_cache` | PASS |
| S2.6 | `turn.complete` prices every turn's tokens into the ledger | `mod-usage` | `hooks/router.ts`, `scripts/modelrouter/hooks.py` `mod_usage`, `registry.cost` | Unit, Node | `Mod.test_usage_is_priced_from_the_registry`; `ModPayloads.test_usage_is_logged_for_its_agent`. Haiku's long-prompt rate is not applied: in progress (m1-04) | PARTIAL |
| S2.7 | `session.start` marks the session so the fallback stands down; every turn and spawn marks it again after `/clear` | `mod-start`, `hooks.mod_active` | `hooks/router.ts`, `scripts/modelrouter/hooks.py` | Unit | `Mod.test_the_settings_hook_stands_down_once_the_mod_is_running`; `Mod.test_turns_and_spawns_keep_the_mod_marked_after_a_clear` | PASS |
| S2.8 | Plugin options from `register(on, options)` reach the runtime as `CLAUDE_PLUGIN_OPTION_*` | `optionEnv` | `hooks/router.ts` | Node | "plugin options reach the CLI env" | PASS |
| S2.9 | Every mod hook fails open, `turn.step` included; `ask()` never throws | `.catch` on each hook; `ask` returns null on any error | `hooks/router.ts` | Node | "every hook falls back to next(e) when it throws"; "a failing or garbled CLI leaves every event unchanged"; "a CLI that throws after next(e) ran does not run next again" | PASS |
| S2.10 | Fallback `PreToolUse` rewrite of the `Agent` call, standing down when the mod runs, alone owning the refused-rewrite guard | `pre_agent`; `calls.refused_rewrite` | `scripts/modelrouter/hooks.py`, `calls.py`, `hooks/hooks.json` | Unit, live | `Router.test_a_rewrite_changes_two_fields_and_approves_nothing`; `Mod.test_the_settings_hook_stands_down_once_the_mod_is_running`; `Router.test_decide_needs_only_its_facts`; live 2.1.330 Agent call path | PASS |
| S2.11 | `PostToolUse`, `PostToolUseFailure`, `SubagentStop` record the model that ran, model errors and background results; a background call finishes only when it stops with an answer | `post_agent`, `agent_failed`, `agent_stopped` | `scripts/modelrouter/hooks.py`, `calls.py` | Unit | `Hooks.test_results_teach_the_registry_which_models_really_run`; `Hooks.test_model_errors_point_to_the_next_model_and_other_errors_dont_count`; `Mod.test_a_background_agent_that_stops_without_an_answer_is_not_a_result`; `Shipped.test_agent_stopped_runs_before_the_next_step` | PASS |
| S2.12 | `SessionStart`, `UserPromptSubmit`, `PostModelSwitch`, `Stop`: session state, request profiles (pasted prompts included), checks before a turn ends | `session_start`, `user_prompt`, `model_switch`, `stop` | `scripts/modelrouter/hooks.py`, `gates.py` | Unit | `Hooks.test_session_start_explains_automatic_routing`; `Hooks.test_a_prompt_that_opens_with_pasted_text_is_the_users_request`; `Hooks.test_model_switch_is_recorded`; `Gates` class | PASS |
| S2.13 | A refused rewrite never loops; identical calls sent together are not taken for refused rewrites | Pending rewrites kept until result; same-moment copies routed | `scripts/modelrouter/calls.py` | Unit | `RepeatGuard.test_the_same_call_again_without_a_result_passes_through`; `RepeatGuard.test_copies_of_one_call_issued_together_are_all_routed` | PASS |
| S2.14 | Profile: five levels from the brief and the agent's name; negated clauses don't count as changes | `profile_call`, `profile_prompt` | `scripts/modelrouter/profile.py` | Unit | `Profile.test_call_levels`; `Profile.test_reading_about_something_sensitive_or_broad_is_still_a_lookup` ("Do not change any files"); `Profile.test_agent_name_sets_a_floor_or_a_ceiling` | PASS |
| S2.15 | Ladder and registry: level, priority and learned raises give model and effort; registry knows models, usability, cost, cap | `ladder.pick`, `registry.select`, `registry.clamp` | `scripts/modelrouter/ladder.py`, `registry.py`, `config/models.json` | Unit | `Registry.test_select_by_priority`; `Registry.test_select_uses_cost_and_speed_not_just_rank`; `Registry.test_cap_limits_available_models`; `Shipped.test_levels_and_models_are_complete` | PASS |
| S2.16 | Decision is a pure function of its facts with the documented precedence; lowered to the cap, never raised; unknown caller models left alone | `router.decide` | `scripts/modelrouter/router.py` | Unit | `Router.test_decide_needs_only_its_facts`; `Router.test_the_users_environment_settings_win`; `Router.test_a_model_named_on_the_call_is_lowered_to_the_cap`; `Router.test_rules_in_override_files`; `Router.test_pins_can_be_overridden`; `Registry.test_clamp_never_raises_above_what_was_asked`; `Router.test_a_call_naming_a_model_outside_the_registry_is_left_alone` | PASS |
| S2.17 | One spawn procedure for both paths; one ledger row; `calls` alone keeps per-call records | `spawn.run`; `calls` | `scripts/modelrouter/spawn.py`, `calls.py` | Unit | `Calls` class; `Feedback.test_a_spawn_identifies_its_call_once_and_reuses_the_raises` | PASS |
| S2.18 | Escalation and learning: a redo runs one level higher, never at less effort; enough redos raise a level; a model the user named is never a vote | `learn.decided`, `learn.raises` | `scripts/modelrouter/learn.py` | Unit | `RepeatGuard.test_a_call_redone_after_its_result_runs_one_level_up`; `RepeatGuard.test_a_redo_at_the_top_of_the_ladder_gets_more_effort`; `Feedback.test_redoing_routed_calls_on_a_stronger_model_raises_the_level`; `Feedback.test_a_model_the_user_named_casts_no_votes` | PASS |
| S2.19 | Settings resolved once per hook | `settings.resolve` | `scripts/modelrouter/settings.py` | Unit | `Options.test_environment_facts`; `Options.test_skill_commands_read_options_recorded_at_session_start` | PASS |
| S2.20 | Availability: failing, substituted or rejected models routed around and retried; `availableModels` and `deniedModels` applied as Claude Code applies them, drop-ins included | `registry.record_failure`, `record_result`, `permitted` | `scripts/modelrouter/registry.py` | Unit | `Registry.test_two_model_errors_route_around_a_model_and_outages_grow`; `Registry.test_two_substitutions_route_around_a_model`; `RepeatGuard.test_a_model_that_never_runs_is_routed_around_after_two_refused_rewrites`; `Registry.test_managed_drop_in_files_merge_with_the_managed_file` | PASS |
| S2.21 | Upstream watch: changelog, docs index, aliases, deprecations, Models API daily; older session versions flagged at session start | `upstream.refresh`, `diff`, `behind` | `scripts/modelrouter/upstream.py` | Unit | `Upstream` class, incl. `test_a_version_older_than_its_familys_newest_is_named_at_session_start` | PASS |
| S2.22 | Ledger and spend: every decision, reason, list-price cost; one-hour cache writes at their own rate; unpriced shown as such; writes atomic, locked, bounded | `store.append_ledger`, `registry.cost` | `scripts/modelrouter/store.py`, `registry.py` | Unit | `Mod.test_usage_is_priced_from_the_registry` (1-hour writes); `Reports.test_usage_report` ("No price in the registry for"); `Store` class | PASS |
| S2.23 | Gates: a test run after code changes in the project; a stronger model's check when a weak session answered a hard request alone | `gates.before_stop` | `scripts/modelrouter/gates.py` | Unit | `Gates` class (13 tests) | PASS |
| S2.24 | Both paths independently operational and testable | Mod and fallback adapters around `spawn.run` | `hooks/router.ts`, `scripts/modelrouter/hooks.py` | Unit, Node | `Mod` and `Router` classes; Node suite runs the mod with a stub CLI | PASS |
| S2.25 | The router never changes which agent runs, never approves a tool call, never blocks a spawn or a session | Rewrites touch `model` and `effort` only | `scripts/modelrouter/hooks.py` `pre_agent` | Unit | `Router.test_a_rewrite_changes_two_fields_and_approves_nothing`. One deliberate exception: a repeated call whose untouched model would break the cap is denied (`RepeatGuard.test_a_refused_rewrite_is_denied_when_the_untouched_call_would_break_the_cap`) | PARTIAL |

## Section 3: universal coverage

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S3.1 | Built-in, user, project and plugin agents routed | Agent discovery across project, user, own and other plugins | `scripts/modelrouter/agents.py` | Unit, live | `AgentDiscovery` class; `Router.test_builtin_explore_is_always_written_out`; live 2.1.330: another plugin's agents routed | PASS |
| S3.2 | Teammates routed | `agent.spawn` with `isTeammate` | `hooks/router.ts` | Unit | `Mod.test_workflow_stages_and_teammate_roles_without_a_file_are_routed`. No teammate spawned live | PARTIAL |
| S3.3 | Workflow stages and agents other plugins' skills start routed | `agent.spawn` with `workflow` | `hooks/router.ts` | Unit, live | `Mod.test_workflow_stages_and_teammate_roles_without_a_file_are_routed`; live 2.1.330 workflow stage on Haiku | PASS |
| S3.4 | Inline skills get the main loop's turn effort | Main-loop `turn.step` | `hooks/router.ts` | Inspection | Follows from S2.5; no test or live run of an inline skill on its own | PARTIAL |
| S3.5 | Forked skills and conversation forks stay on the parent's model | `e.fork` pass-through | `hooks/router.ts`, `scripts/modelrouter/router.py` | Unit, Node | `Router.test_fork_is_never_touched`; "forks and resumed runs pass through with no CLI call". Forked skills on the mod path are UNCONFIRMED in `docs/research/model-control.md` | PARTIAL |
| S3.6 | An agent, plugin or skill never seen before is routed sensibly with no configuration | Profile from brief and name | `scripts/modelrouter/profile.py`, `agents.py` | Unit | `AgentDiscovery.test_other_plugin_by_install_record`; `Profile.test_agent_name_sets_a_floor_or_a_ceiling`. An agent with no definition on disk that the engine didn't make is left alone (`Router.test_unknown_agents_and_unknown_models_are_left_alone`) | PARTIAL |
| S3.7 | Every surface: terminal, Desktop, VS Code, JetBrains, `-p`, Agent SDK | Surface-independent hooks | `hooks/` | Live | Live 2.1.330: terminal and `-p` on macOS only | PARTIAL |
| S3.8 | Every permission mode, auto mode included | Never approves; mod sets model after review | `scripts/modelrouter/hooks.py`, `hooks/router.ts` | Live | Live 2.1.330: default and auto mode. Plan, acceptEdits and bypass not run | PARTIAL |
| S3.9 | Subscription and API-key logins | Models API read only with a direct key | `scripts/modelrouter/upstream.py` | Unit, live | `Upstream.test_models_api_is_read_only_with_a_direct_api_key`. The live record doesn't say which login each run used | PARTIAL |
| S3.10 | Bedrock, Vertex, Foundry to the extent they allow | Aliases only; main effort skipped there | `scripts/modelrouter/hooks.py` `mod_turn`, `settings.py` | Unit | `Mod.test_turn_effort_is_left_alone_where_an_effort_change_drops_the_cache`. No live run on any provider | PARTIAL |
| S3.11 | `availableModels`, `deniedModels`, managed settings and `managed-settings.d` honored | `registry.permitted` | `scripts/modelrouter/registry.py` | Unit | `Registry.test_a_managed_allowlist_applies_alone`; `Registry.test_managed_drop_in_files_merge_with_the_managed_file`; `Registry.test_denied_models_outside_managed_settings_are_ignored` | PASS |
| S3.12 | `CLAUDE_CODE_SUBAGENT_MODEL`, `_FORCE`, `CLAUDE_CODE_EFFORT_LEVEL`, `CLAUDE_CODE_SUBAGENT_EFFORT` honored | `router.decide` precedence | `scripts/modelrouter/router.py` | Unit, live | `Router.test_the_users_environment_settings_win`; `Router.test_a_forced_subagent_model_still_gets_its_effort`; `Mod.test_forced_subagent_model_gets_effort_only`; live 2.1.330 under FORCE | PASS |
| S3.13 | `disableAllHooks` and `allowManagedHooksOnly` honored | Claude Code runs no plugin hook then | n/a | Inspection | No test or live run with either set | PARTIAL |
| S3.14 | "Verified live" in `docs/ARCHITECTURE.md` kept as the capability registry, with version and method | Section exists | `docs/ARCHITECTURE.md` "Verified live" | Inspection | Names 2.1.330, headless and interactive auto mode, each check | PASS |

## Section 4: cost and quality engineering

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S4.1 | Cost per solved task is the measure | None measured | n/a | n/a | No suite or results exist (Section 14) | FAIL |
| S4.2 | Effort tuning before model switching | complex and hardest share Opus; a redo adds effort first | `config/models.json` levels, `scripts/modelrouter/learn.py` | Unit | `RepeatGuard.test_a_redo_held_on_the_same_model_by_the_cap_gets_more_effort`; `RepeatGuard.test_a_call_redone_after_its_result_runs_one_level_up` | PASS |
| S4.3 | Cheap first, verify, escalate on a failed check | Ladder starts low; redo escalation; Stop gates | `scripts/modelrouter/ladder.py`, `learn.py`, `gates.py` | Unit | `Router.test_agents_without_a_model_get_the_one_their_subtask_needs`; `RepeatGuard` class; `Gates` class | PASS |
| S4.4 | Prompt-cache awareness: one model for the main loop, no switch that re-reads the context uncached | Main loop gets effort only, skipped where it drops the cache | `hooks/router.ts`, `scripts/modelrouter/hooks.py` | Unit | `Mod.test_turn_effort_is_left_alone_where_an_effort_change_drops_the_cache`; the mod sets no main-loop model (inspection) | PASS |
| S4.5 | Fan-outs that share a prefix | Copies of one brief get the same model and effort | `scripts/modelrouter/calls.py` | Unit | `Mod.test_copies_of_one_brief_launched_in_the_background_all_run_at_its_level`. Cache hits not measured | PARTIAL |
| S4.6 | One-hour cache writes priced at their own rate | `cache_write_1h` | `scripts/modelrouter/registry.py` `cost` | Unit | `Mod.test_usage_is_priced_from_the_registry` | PASS |
| S4.7 | Haiku's long-prompt rate | Not applied | `scripts/modelrouter/registry.py` `cost` (shortcut comment) | n/a | In progress (m1-04) | PARTIAL |
| S4.8 | Fast mode and agent-team cost | No fast-mode rate in `prices`; teammates priced as any agent's turn | `config/models.json` | Inspection | Grep finds no fast-mode price | FAIL |
| S4.9 | Learned routing from real redos and failures only | Votes from redos and named stronger models | `scripts/modelrouter/learn.py` | Unit | `Feedback` class | PASS |
| S4.10 | Low hook overhead, latency measured against hook timeouts | Hook timeouts 5 to 10 s, mod `timeoutMs` 8000 | `hooks/hooks.json`, `hooks/router.ts` | Live | One figure: a spawn decision took about 110 ms in live runs (`docs/ARCHITECTURE.md`). No recorded measurement | PARTIAL |
| S4.11 | Measure with `claude plugin eval`, the ledger, `/model-router:usage` and live sessions | Evals exist under `evals/` | `evals/` | n/a | Evals not rerun since written | PARTIAL |
| S4.12 | Measure cost per solved task, pass rate, tokens, accuracy per level, escalation rate, latency, cache hit rate | None | n/a | n/a | No measurements | FAIL |
| S4.13 | Test small and large tasks, fan-outs, cold and warm caches, every priority | None | n/a | n/a | No suite | FAIL |
| S4.14 | Publish reproducible results with version, models, settings, tasks, commands and live-run cost | None | n/a | n/a | No `docs/BENCHMARKS.md` | FAIL |
| S4.15 | Beat every baseline on cost per solved task at equal or higher pass rate | None | n/a | n/a | No comparison | FAIL |

## Section 5: plugin engineering

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S5.1 | Runtime uses the standard library only, on Python 3.9, tested in CI on Linux, macOS and Windows | stdlib imports only | `scripts/` | Grep, CI | Imports: argparse, contextlib, dataclasses, datetime, hashlib, itertools, json, os, pathlib, re, sys, time, typing, urllib, xml. CI main: 3.9 and 3.13 on all three | PASS |
| S5.2 | The mod stays thin and hands every decision to the runtime | Mod forwards events | `hooks/router.ts` | Inspection | One decision stays in the mod: `turn.step` compares effort ranks so the main loop's effort only rises | PARTIAL |
| S5.3 | The launcher works on `sh` and on Windows | `model-router.sh` | `scripts/model-router.sh` | Unit, CI | `Launcher.test_launcher_runs_hooks_and_commands` passed on windows-latest 3.13 in CI main (log checked) | PASS |
| S5.4 | Components match `GLOSSARY.md`; a term is added for every named module or concept | Glossary of 13 terms | `GLOSSARY.md` | Inspection | No entry for registry, profile, priority, gate, fork or model health | PARTIAL |
| S5.5 | No dead code, duplicated logic or unjustified abstraction | Dead-code trim before 0.1.0 | `scripts/modelrouter/` | Review | Not rechecked in this pass | PARTIAL |
| S5.6 | Documented interfaces only; undocumented binary behaviour recorded and covered live | Research doc records binary checks and UNCONFIRMED items | `docs/research/model-control.md` | Inspection, live | `agent.spawn` fields from a binary check, covered by live 2.1.330 on macOS only | PARTIAL |

## Section 6: enterprise and future-proofing

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S6.1 | Linux, macOS, Windows declared only after install and run, with how | Unit tests on all three | `.github/workflows/ci.yml` | CI, live | CI main on all three; installed and run live on macOS only | PARTIAL |
| S6.2 | Fail-open on every hook and error path | Handlers catch and exit 0; launcher exits 0 without Python | `scripts/modelrouter/hooks.py`, `scripts/model-router.sh`, `hooks/router.ts` | Unit, Node | `Router.test_hooks_never_break_on_garbage`; "every hook falls back to next(e) when it throws" | PASS |
| S6.3 | Trust model: user file trusted, project file limited, project names never take shipped IDs | `config` overrides | `scripts/modelrouter/config.py`, `registry.py` | Unit | `Registry.test_project_can_add_a_model_but_not_rerank_or_slip_under_the_cap`; `Registry.test_a_project_model_name_cannot_capture_a_trusted_models_ids`; `Options.test_files_can_weaken_but_not_strengthen_the_mode` | PASS |
| S6.4 | Whether a project file may raise levels and effort | 0.1.0 allows raises | `scripts/modelrouter/config.py` | n/a | Waiting on the user (audit item r1-04) | PARTIAL |
| S6.5 | Atomic state writes under a lock, safe when many agents start at once | `store.write_atomic`, `store.locked` | `scripts/modelrouter/store.py` | Unit | `Store.test_updates_from_processes_running_at_once_are_all_kept`; `Store.test_a_stale_lock_is_cleared_and_a_held_one_never_hangs` | PASS |
| S6.6 | State corruption detection and recovery | Ledger skips junk lines; state files not yet | `scripts/modelrouter/store.py` | Unit | `Store.test_ledger_survives_junk_lines`. State files: in progress (m1-01) | PARTIAL |
| S6.7 | Versioned state and migration | None in 0.1.0 | `scripts/modelrouter/store.py` | n/a | In progress (m1-01) | PARTIAL |
| S6.8 | Bounded state and ledger size | 200 sessions per keyed file; ledger trimmed at 4 MB | `scripts/modelrouter/store.py` | Unit | `Store.test_keyed_files_drop_the_least_recently_written`; `Store.test_two_hooks_trimming_the_ledger_at_once_keep_half_of_it` | PASS |
| S6.9 | Offline: the daily check fails quietly; an empty feed is a failed fetch | `upstream.refresh` | `scripts/modelrouter/upstream.py` | Unit | `Upstream.test_a_failed_fetch_leaves_the_cache_alone`; `Upstream.test_an_empty_feed_is_an_error_not_data` | PASS |
| S6.10 | No code, prompt or brief leaves the machine; network only from the daily fetch; API key to `api.anthropic.com` alone, no redirect | `urllib` only in `upstream.http_get` | `scripts/modelrouter/upstream.py` | Grep, unit | Grep: `urllib` appears only in `upstream.py`, hosts `code.claude.com`, `platform.claude.com`, `api.anthropic.com`; `Upstream.test_the_api_key_never_follows_a_redirect` | PASS |
| S6.11 | No dependencies | stdlib only | `scripts/` | Grep | See S5.1 | PASS |
| S6.12 | CI actions pinned to commit SHAs | Each action pinned to a commit SHA, its version in a comment (after 0.1.0) | `.github/workflows/ci.yml`, `upstream-watch.yml` | Inspection | Pinned in `ci.yml` and `upstream-watch.yml`; not yet run in CI | PARTIAL |
| S6.13 | A reviewed marketplace manifest | `.claude-plugin/marketplace.json` | `.claude-plugin/marketplace.json` | Unit, validate | `Shipped.test_manifest_and_marketplace`; `claude plugin validate --strict .` passed | PASS |
| S6.14 | Machine-readable output for `/model-router:usage` and `/model-router:models` with a versioned `schema` | Only `status --json` in 0.1.0 | `scripts/modelrouter/report.py`, `cli.py` | n/a | In progress (m1-02) | PARTIAL |
| S6.15 | Stable, versioned options and config shapes, with a compatibility policy | None | `.claude-plugin/plugin.json`, `config/models.json` | Inspection | No version field on the config files and no written policy | FAIL |
| S6.16 | Automated checks that fail when Claude Code moves under the plugin | Daily `upstream-watch.yml`; mod payload fixtures | `.github/workflows/upstream-watch.yml`, `tests/fixtures/` | Unit | "each payload the mod sends matches its fixture"; `Upstream.test_diff_flags_releases_watched_pages_and_model_changes`. The workflow has not run on `main` yet (`gh run list --workflow upstream-watch.yml` is empty) | PARTIAL |

## Section 7: project identity

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S7.1 | Named Claude Code Model Router, plugin `model-router`, commands `/model-router:`; the earlier name never returns | Manifest name | `.claude-plugin/plugin.json` | Unit | `Shipped.test_nothing_still_carries_the_old_name`; `Shipped.test_readme_and_description_keep_their_search_phrases` | PASS |
| S7.2 | No claim of Anthropic endorsement, or of being the only or official router | Wording | `README.md`, `docs/`, manifests | Grep | Grep for "official", "endorse", "only router": the one hit is "the official changelog" (Anthropic's) | PASS |

## Section 8: building the product

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S8.1 | The state documents exist: traceability, release readiness, roadmap | This file, `RELEASE_READINESS.md`, `ROADMAP.md` | `docs/` | Unit | `tests/test_traceability.py` | PASS |
| S8.2 | Documentation and install steps true to the code | README, ARCHITECTURE, CHANGELOG | repo docs | Inspection | `CHANGELOG.md` 0.1.0 says unit tests ran on Python 3.9, 3.10 and 3.13; `ci.yml` runs 3.9 and 3.13 only | PARTIAL |
| S8.3 | Security and reliability testing repeated after each significant change | Unit tests only | `tests/` | n/a | No security assessment on file | PARTIAL |
| S8.4 | Dogfood: the team's subagents run through the router and the ledger is read as evidence | Plugin loaded in team sessions | n/a | n/a | The ledger was not read in this pass | PARTIAL |

## Section 9: quality assurance

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S9.1 | Gate: `python3 -m unittest discover -s tests` | Unit suite | `tests/` | Local, CI | Local on Python 3.9.6: 183 tests OK; CI main: 180 tests OK on each of 6 jobs | PASS |
| S9.2 | Gate: `node --experimental-strip-types --test "tests/mod/*.test.ts"` | Node suite | `tests/mod/router.test.ts` | Local, CI | Local Node 22.22.3: 12 of 12 pass; CI main `mod` job 12 of 12 | PASS |
| S9.3 | Gate: `claude plugin validate --strict .` and `plugins/model-router` | Manifests | `.claude-plugin/`, `plugins/model-router/.claude-plugin/` | Local, CI | Both "Validation passed" on 2.1.330 locally and in CI main `validate` | PASS |
| S9.4 | Gate: `claude plugin eval plugins/model-router --trust-plugin` | Two routing evals | `evals/` | n/a | Not rerun since the evals were written; a run spends money | PARTIAL |
| S9.5 | Type-check the mod and its test with `tsc` against the installed build's mod API types, tsconfig outside the plugin | n/a | `hooks/router.ts`, `tests/mod/router.test.ts` | Local | `tsc` 6.0.3: the mod passes with full lib checks; mod plus test pass with `--skipLibCheck`, since the 2.1.330 types redeclare globals that clash with `@types/node`. Not in CI. Commands in [TEST_RESULTS.md](TEST_RESULTS.md) | PASS |
| S9.6 | Live verification on the installed Claude Code, headless and interactive auto mode, ledger read back, learned stats reset | Manual | `docs/ARCHITECTURE.md` "Verified live" | Live | Done for 0.1.0 on 2.1.330, macOS. Not rerun since | PARTIAL |
| S9.7 | The traceability matrix is counted by a script, never by hand | `tools/traceability.py` | `tools/traceability.py` | Unit | `tests/test_traceability.py` | PASS |
| S9.8 | Unit, integration, end-to-end, differential, property-based, regression, concurrency, security and cross-platform tests | Unit, integration (launcher), concurrency (store), security (trust, redirect), cross-platform CI | `tests/` | Inspection | No property-based or differential tests | PARTIAL |

## Section 10: release gates

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S10.1 | Every subagent, teammate and workflow stage routed on the mod path | See S2.1 | `hooks/router.ts` | Unit, live | No teammate spawned live | PARTIAL |
| S10.2 | A working fallback where mods don't load | See S2.10 | `scripts/modelrouter/hooks.py` | Unit, live | Live 2.1.330 Agent call path in default and auto mode | PASS |
| S10.3 | Precedence and cap honored in every case | See S2.16 | `scripts/modelrouter/router.py` | Unit | `Router` class; `RepeatGuard.test_the_cap_holds_even_when_a_lowering_rewrite_did_not_take` | PASS |
| S10.4 | Redo escalation and learning never loop and never lower quality | See S2.18 | `scripts/modelrouter/learn.py`, `calls.py` | Unit | `RepeatGuard.test_redo_escalation_stops_at_the_top_and_skips_named_models`; `Feedback.test_feedback_never_goes_below_what_ships` | PASS |
| S10.5 | Availability tracking routes around a model that doesn't work here | See S2.20 | `scripts/modelrouter/registry.py` | Unit | `Router.test_an_unavailable_model_is_routed_around` | PASS |
| S10.6 | The daily upstream check fails quietly offline | See S6.9 | `scripts/modelrouter/upstream.py` | Unit | `Upstream.test_a_failed_fetch_leaves_the_cache_alone` | PASS |
| S10.7 | A spend ledger that matches list prices | See S2.22 | `scripts/modelrouter/registry.py` | Unit | Haiku's long-prompt rate in progress (m1-04); no fast-mode rate | PARTIAL |
| S10.8 | Fail-open behaviour proven by tests | See S2.9, S6.2 | `hooks/router.ts`, `scripts/modelrouter/hooks.py` | Unit, Node | Same tests | PASS |
| S10.9 | Automated tests on Linux, macOS and Windows, oldest and newest Python in CI | CI matrix | `.github/workflows/ci.yml` | CI | CI main: Python 3.9 and 3.13 on all three. The mod's Node tests ran on Linux only in 0.1.0; `ci.yml` now runs them on all three, not yet run in CI | PASS |
| S10.10 | CI green on the release PR and on the merge commit in `main` before tagging | Release checklist | `RELEASING.md` | CI | PR run 45004814197 passed; main run 45004998622 passed at 08:56 UTC; `v0.1.0` points at `8ec8eec`, released 08:57 UTC | PASS |
| S10.11 | Reproducible cost and quality results | None | n/a | n/a | No `docs/BENCHMARKS.md` | FAIL |
| S10.12 | A security assessment | `docs/SECURITY.md` | `docs/SECURITY.md` | n/a | In progress (m1-08) | PARTIAL |
| S10.13 | Professional documentation and install steps, README at release polish with SVG diagrams | README | `README.md` | Inspection | No SVG diagrams; see S8.2 | PARTIAL |

## Section 11: final delivery

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S11.1 | Source and architecture: `docs/ARCHITECTURE.md`, `GLOSSARY.md` | Both exist | `docs/ARCHITECTURE.md`, `GLOSSARY.md` | Inspection | Present | PASS |
| S11.2 | Capabilities and commands in `README.md` | "Commands", "Configuration" | `README.md` | Unit | `Shipped.test_skills_have_descriptions_and_use_the_launcher` | PASS |
| S11.3 | Primary-source research in `docs/research/` | `model-control.md` | `docs/research/` | Inspection | Present, dated 2026-11-22 | PASS |
| S11.4 | Supported versions, surfaces, platforms, providers | README "Compatibility", ARCHITECTURE "Verified live" | `README.md`, `docs/ARCHITECTURE.md` | Inspection | Present | PASS |
| S11.5 | Install and usage instructions | README "Install" | `README.md` | Inspection | Present | PASS |
| S11.6 | Automated test results | `docs/TEST_RESULTS.md` | `docs/TEST_RESULTS.md` | Inspection | Present | PASS |
| S11.7 | Security assessment | `docs/SECURITY.md` | `docs/SECURITY.md` | n/a | In progress (m1-08) | PARTIAL |
| S11.8 | Reproducible cost and quality results with raw data | None | `docs/BENCHMARKS.md` | n/a | Missing | FAIL |
| S11.9 | Hook latency and state-size measurements | None | n/a | n/a | Missing | FAIL |
| S11.10 | Remaining defects, incomplete requirements and release readiness | `docs/RELEASE_READINESS.md` | `docs/RELEASE_READINESS.md` | Inspection | Present | PASS |
| S11.11 | Future engineering roadmap | `docs/ROADMAP.md` | `docs/ROADMAP.md` | Inspection | Present | PASS |
| S11.12 | Evidence-based comparison against the baselines | None | `docs/BENCHMARKS.md` | n/a | Missing | FAIL |
| S11.13 | Release checklist and changelog | `RELEASING.md`, `CHANGELOG.md` | repo root | Inspection | Present | PASS |
| S11.14 | An index of all delivery items in `docs/README.md` | Index | `docs/README.md` | Inspection | Present | PASS |

## Section 12: edge cases and reliability

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S12.1 | Many agents spawned in one message | Copies routed together; votes all counted | `scripts/modelrouter/calls.py`, `learn.py` | Unit | `RepeatGuard.test_copies_of_one_call_issued_together_are_all_routed`; `Feedback.test_votes_from_calls_issued_at_once_are_all_counted` | PASS |
| S12.2 | Background agents | `launched` until `SubagentStop` | `scripts/modelrouter/calls.py` | Unit, live | `Mod.test_a_background_spawn_is_redone_one_level_up_only_after_it_stops`; live 2.1.330 background copies on Haiku | PASS |
| S12.3 | Resumed agents | Pass-through | `hooks/router.ts` | Node | "forks and resumed runs pass through with no CLI call" | PASS |
| S12.4 | Forks | Pass-through | `hooks/router.ts`, `scripts/modelrouter/router.py` | Unit, Node | `Router.test_fork_is_never_touched` | PASS |
| S12.5 | Nested agents | Pick always written inside a subagent on the fallback; parent model known on the mod path | `scripts/modelrouter/router.py` | Unit | `Router.test_inside_a_subagent_the_pick_is_always_written_out` | PASS |
| S12.6 | An agent with no definition on disk | Left alone unless the engine made it | `scripts/modelrouter/router.py` | Unit | `Router.test_unknown_agents_and_unknown_models_are_left_alone` | PASS |
| S12.7 | Compaction | SessionStart matcher includes `compact` | `hooks/hooks.json` | Inspection | No test of a compacted session | PARTIAL |
| S12.8 | `/clear` | Turns and spawns re-mark the mod | `hooks/router.ts`, `scripts/modelrouter/hooks.py` | Unit | `Mod.test_turns_and_spawns_keep_the_mod_marked_after_a_clear` | PASS |
| S12.9 | A session's model changed mid-run | `PostModelSwitch` | `scripts/modelrouter/hooks.py` `model_switch` | Unit | `Hooks.test_model_switch_is_recorded` | PASS |
| S12.10 | A brief in another language | English wording rules | `scripts/modelrouter/profile.py` | n/a | No test | PARTIAL |
| S12.11 | An empty or huge brief | Profile by length and signals | `scripts/modelrouter/profile.py` | Unit | `Profile.test_thorough_or_long_briefs_are_not_lookups` (5,000 characters); no empty-brief or timing test | PARTIAL |
| S12.12 | A brief full of negations | Negated clauses dropped | `scripts/modelrouter/profile.py` | Unit | `Profile.test_words_that_only_look_like_changes` | PASS |
| S12.13 | A prompt that starts with pasted text | Pasted block is the user's request | `scripts/modelrouter/hooks.py` `is_user_prompt` | Unit | `Hooks.test_a_prompt_that_opens_with_pasted_text_is_the_users_request` | PASS |
| S12.14 | Two plugins' mods setting the same spawn | Ledger records the model that ran | `scripts/modelrouter/hooks.py` | Inspection | Documented limit in ARCHITECTURE; not tested | PARTIAL |
| S12.15 | Auto mode refusing a rewrite | Refused-rewrite pass-through | `scripts/modelrouter/calls.py` | Unit, live | `RepeatGuard.test_claude_reissuing_the_rewritten_call_is_fine`; live 2.1.330: a rejected rewrite ran untouched on the retry | PASS |
| S12.16 | A provider missing an alias | Failure or substitution routes around it | `scripts/modelrouter/registry.py` | Unit | `Registry.test_two_model_errors_route_around_a_model_and_outages_grow`; `Registry.test_two_substitutions_route_around_a_model` | PASS |
| S12.17 | A model retired between two sessions | Lifecycle from deprecations page and Models API | `scripts/modelrouter/upstream.py` | Unit | `Upstream.test_retiring_models_are_flagged_when_new_or_in_use` | PASS |
| S12.18 | A price table missing a model | Shown as unpriced | `scripts/modelrouter/report.py` | Unit | `Reports.test_usage_report` | PASS |
| S12.19 | Edits to files outside the project | Ignored by the verify gate | `scripts/modelrouter/gates.py` | Unit | `Gates.test_edits_outside_the_project_dont_count` | PASS |
| S12.20 | Clock changes | Time windows on health and redos | `scripts/modelrouter/registry.py`, `calls.py` | n/a | No test of a clock moving backwards | PARTIAL |
| S12.21 | A corrupt or half-written state file | Atomic writes; ledger skips junk | `scripts/modelrouter/store.py` | Unit | `Store.test_ledger_survives_junk_lines`. State files: in progress (m1-01) | PARTIAL |
| S12.22 | A locked state file | Lock skipped after two seconds | `scripts/modelrouter/store.py` `locked` | Unit | `Store.test_a_stale_lock_is_cleared_and_a_held_one_never_hangs` | PASS |
| S12.23 | A missing Python | Launcher exits 0 | `scripts/model-router.sh` | Inspection | No test runs the launcher without Python | PARTIAL |
| S12.24 | Windows paths and line endings | Path handling; BOM tolerated | `scripts/modelrouter/agents.py` | Unit, CI | `AgentDiscovery.test_a_file_saved_with_a_byte_order_mark`; CI main on Windows | PASS |
| S12.25 | A CI runner image that drops an old Python | 3.9 Linux job pinned to `ubuntu-24.04` | `.github/workflows/ci.yml` | CI, unit | `Shipped.test_ci_tests_the_oldest_python_the_launcher_accepts`; CI main `test (ubuntu-24.04, 3.9)` | PASS |
| S12.26 | A Claude Code release that renames an event or a field | Payload fixtures; daily upstream check | `tests/fixtures/`, `.github/workflows/upstream-watch.yml` | Node | "each payload the mod sends matches its fixture"; a rename in the binary is not detected automatically | PARTIAL |
| S12.27 | State lifecycle: atomic writes, locking, bounded growth, corruption detection, recovery, cleanup, versioned migrations | See S6.5 to S6.8 | `scripts/modelrouter/store.py` | Unit | Corruption and migrations in progress (m1-01) | PARTIAL |
| S12.28 | Commands with clear output, machine-readable modes, a `reset` for everything learned | Five skills; `models --reset` | `skills/`, `scripts/modelrouter/cli.py` | Unit | `Feedback.test_redoing_routed_calls_on_a_stronger_model_raises_the_level` ("cleared"). Machine-readable modes in progress (m1-02) | PARTIAL |
| S12.29 | Untrusted project config and prompt text can't steer routing past the user's cap | Project limits; named models lowered to the cap; prompt model only a reminder | `scripts/modelrouter/config.py`, `router.py`, `hooks.py` | Unit | `Router.test_a_model_named_on_the_call_is_lowered_to_the_cap`; `Hooks.test_a_model_the_user_may_be_asking_for_is_left_to_claude`; `Registry.test_project_can_add_a_model_but_not_rerank_or_slip_under_the_cap` | PASS |
| S12.30 | Never execute repository code while routing | Agent files read as text | `scripts/modelrouter/agents.py` | Grep | No `subprocess`, `exec` or `eval` in the runtime | PASS |

## Section 13: stack rules

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S13.1 | Python 3 standard library only at runtime | See S5.1 | `scripts/` | Grep | Imports checked | PASS |
| S13.2 | The mod is TypeScript loaded as is, with no routing logic of its own | See S5.2 | `hooks/router.ts` | Inspection | The `turn.step` effort-rank check | PARTIAL |
| S13.3 | The launcher is `sh` and harmless without Python | See S5.3, S12.23 | `scripts/model-router.sh` | Unit | Missing-Python path not tested | PARTIAL |
| S13.4 | Tests on the oldest and newest Python CI covers, on Linux, macOS and Windows; pin an image rather than drop a version | CI matrix | `.github/workflows/ci.yml` | CI | CI main | PASS |
| S13.5 | Network only to the hosts the README names, only from the daily check | See S6.10 | `scripts/modelrouter/upstream.py` | Grep | Hosts match README "Install" | PASS |
| S13.6 | No external project, library, benchmark or tool cited without a primary source | Research doc cites docs | `docs/` | n/a | Not audited in this pass | PARTIAL |

## Section 14: cost and quality leadership

| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S14.1 | Baselines run: defaults, one model per agent at default and `high`, `opusplan` and the advisor | None | n/a | n/a | Not built | FAIL |
| S14.2 | A pinned suite: task classes, shapes, fixed repos and commits, pass checks, cold and warm cache, router overhead | None | n/a | n/a | Not built | FAIL |
| S14.3 | Results per task class and configuration with pass rate, cost per solved task, 95% intervals and rank | None | n/a | n/a | Not built | FAIL |
| S14.4 | The cheap routing evals run in CI and a regression blocks the change | Evals exist, not in CI | `evals/`, `.github/workflows/ci.yml` | Inspection | `ci.yml` has no eval job | FAIL |
