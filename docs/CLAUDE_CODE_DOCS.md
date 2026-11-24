<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><img src="assets/banner-claude-code-docs.svg" width="880" alt="Built on the docs: Which Claude Code docs does it follow, and where?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="SECURITY.md">Security</a> &nbsp;&middot;&nbsp; Next: <a href="BENCHMARKS.md">Benchmarks</a></p>
<!-- End of page head. -->

# Built on the Claude Code docs

Model Router uses only the extension points Claude Code documents: the plugin manifest, hooks, the mods API, skills and subagents. It follows the rules the Claude Code docs, the Claude platform docs and Anthropic's best practices set on subagent models, effort settings, the prompt cache, hooks, mods and packaging. This page lists each one: what the docs say, with a link to the page; what the plugin does; and the test or live run that checks it. The places it relies on something the docs don't state, does something other than they advise, or goes further than they do are at the end, in [Where it goes past the docs](#where-it-goes-past-the-docs).

Last checked against the live docs on 2026-11-24, on Claude Code 2.1.330 and 2.1.331. Row numbers such as S3.12 point to [TRACEABILITY.md](TRACEABILITY.md), and test names are in `tests/`.

## Choosing a subagent's model

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| A subagent's model comes from the first of: the call's `model`, the agent's `model`, `CLAUDE_CODE_SUBAGENT_MODEL`, then the conversation's model ([sub-agents](https://code.claude.com/docs/en/sub-agents.md#choose-a-model)) | Fills in a model only when none of the first three set one, and changes no model under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` | `test_the_users_environment_settings_win`, live (S3.12) |
| An agent's `model` field takes an alias such as `haiku` or a full model ID ([frontmatter](https://code.claude.com/docs/en/sub-agents.md#supported-frontmatter-fields)) | Keeps an agent's own model by default (`agent_pins` is `respect`). Its three light agents, scout, verifier and docs-researcher, pin `haiku` | `test_pins_can_be_overridden` (S2.16) |
| A teammate's model comes from the spawn prompt, its definition, `CLAUDE_CODE_SUBAGENT_MODEL`, then the lead, and a mod's `agent.spawn` model takes the first place ([agent teams](https://code.claude.com/docs/en/agent-teams.md#specify-teammates-and-models)) | Routes teammates through `agent.spawn` | `test_workflow_stages_and_teammate_roles_without_a_file_are_routed`, live (S3.2) |
| Workflow agents get their model in the same order as subagents ([workflows](https://code.claude.com/docs/en/workflows.md#cost)) | Routes each workflow stage like a subagent, and keeps a model the script names | Live (S3.3), and under ultracode on 2.1.331 |
| Ultracode runs substantive tasks as workflows at the session's effort level, `--effort ultracode` sets it to `xhigh`, and on the main conversation's model a subagent or teammate with no other level runs at that level ([model configuration](https://code.claude.com/docs/en/model-config.md#effort-level-of-subagents-and-teammates)) | Never sets an effort below the session's level for a subagent, teammate or workflow agent the main conversation starts on its model, on either path | `test_an_agent_on_the_sessions_own_model_runs_at_least_at_the_sessions_effort`, Node test "the main loop's effort reaches mod-spawn, and the agent steps at the effort the CLI decided", live under ultracode on 2.1.331 |
| `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` runs every workflow agent on one model ([changelog](https://code.claude.com/docs/en/changelog.md), 2.1.296) | Sets only a workflow agent's effort while it is set | `test_a_workflow_model_pin_keeps_its_model_and_the_router_sets_only_effort`, live on 2.1.331 |
| A fork sees the same model and history as its parent ([forks](https://code.claude.com/docs/en/sub-agents.md#fork-the-current-conversation)) | Passes forks and resumed runs through untouched | `test_fork_is_never_touched`, live (S2.2) |

## Effort

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| An agent's `effort` field overrides the session's effort, and a call can pass its own `effort` from Claude Code 2.1.292 ([effort level](https://code.claude.com/docs/en/sub-agents.md#choose-an-effort-level)) | Sets a call's effort only when nobody else chose it: the caller, the agent, `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` | `test_an_agents_own_effort_and_a_callers_effort_are_kept` (S2.3) |
| `CLAUDE_CODE_SUBAGENT_EFFORT` doesn't apply to forks, forked skills or teammates ([environment variables](https://code.claude.com/docs/en/env-vars.md#variables)) | Sets a teammate's effort itself | `test_the_subagent_effort_variable_does_not_reach_a_teammate` (S3.12) |
| A `maxEffortLevel` cap still applies under `CLAUDE_CODE_EFFORT_LEVEL` ([environment variables](https://code.claude.com/docs/en/env-vars.md#variables)) | Holds every effort it sets to `maxEffortLevel`, managed settings included | `test_a_max_effort_level_holds_every_effort_the_router_sets` |
| For agentic coding, start at `medium` for well-specified tasks and move to `high` for harder ones ([effort](https://platform.claude.com/docs/en/build-with-claude/effort)) | Lookup and standard run at `medium`, complex at `high` | `test_the_shipped_ladder_picks_at_balanced_priority` (S4.2) |
| Run every task at a low setting and re-run only the failures higher: about 97% solved for about $0.17 a task, against 95.3% for $0.29 with every task at high ([cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence#re-run-failures-at-higher-effort)) | Its redo is modeled on this: a call re-issued after its result came back runs one level higher | `test_a_call_redone_after_its_result_runs_one_level_up` (S4.3) |

## The prompt cache

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| Switching the conversation's model means the next request reads the whole history with no cache hits ([prompt caching](https://code.claude.com/docs/en/prompt-caching.md#switching-models)) | Never switches the main conversation's model. It can raise that turn's effort instead (`main_effort`, off by default) | `test_turn_effort_is_left_alone_where_an_effort_change_drops_the_cache` (S4.4) |
| Changing effort keeps the cache on Fable 5.1 and later, Opus 5.5, Sonnet 5.5 and Haiku 5.5, with an API key or a Claude subscription ([changing effort](https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level)) | `main_effort` changes effort only there | `test_turn_effort_stays_off_without_a_first_party_credential` |
| Workflow agents with the same model, effort, agent type, tools and folder share a cache prefix ([fan-out](https://code.claude.com/docs/en/workflows.md#prompt-caching-in-a-fan-out)) | Copies of one brief get the same model and effort | `test_copies_of_one_brief_launched_in_the_background_all_run_at_its_level` (S4.5) |

## Hooks

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| `updatedInput` replaces the whole tool input, so include the fields you don't change ([PreToolUse](https://code.claude.com/docs/en/hooks.md#pretooluse-decision-control)) | The fallback rewrite keeps every field and changes only `model` and `effort` | `test_a_rewrite_changes_two_fields_and_approves_nothing` (S2.25) |
| Exit code 2 blocks, any other failure lets the action go ahead, and a timed-out PreToolUse hook doesn't block ([exit codes](https://code.claude.com/docs/en/hooks.md#exit-code-output)) | Every hook exits 0 or 1, never 2, and exits 0 when Python is missing | `test_hooks_never_break_on_garbage` (S6.2), `test_every_hook_exits_0_and_a_command_explains` (S12.23) |
| A matcher made of letters, digits and pipes is an exact list, and SessionStart fires on startup, resume, clear, compact and fork ([matchers](https://code.claude.com/docs/en/hooks.md#matcher-patterns)) | Its session hook matches all five sources | `test_a_compaction_keeps_the_request_and_routed_calls_and_the_model` (S12.7) |
| The Stop input's `background_tasks` lists the tasks still in flight, such as a `workflow` or a `subagent` ([Stop input](https://code.claude.com/docs/en/hooks.md#stop-input)) | The checks before a turn ends wait while a workflow or a subagent is in flight | `test_a_workflow_or_agent_still_running_holds_the_gates_and_keeps_the_ask` |
| A hook's input carries `effort.level`, the level when the hook runs ([common input fields](https://code.claude.com/docs/en/hooks.md#common-input-fields)) | The fallback reads it as the main conversation's level, which a subagent on the session's own model runs at | `test_the_sessions_effort_counts_only_for_a_call_from_the_main_conversation` |
| An Agent result reports `resolvedModel`, `modelsUsed` when the model was swapped, and `totalTokens` from the final request only ([hooks](https://code.claude.com/docs/en/hooks.md)) | Records the model that really ran, and labels `totalTokens` as the final request's | `test_results_teach_the_registry_which_models_really_run` |

## The mods API

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| A plugin's mod is listed under `modules` in `hooks/hooks.json` and exports `register(on, options)` ([mods reference](https://code.claude.com/docs/en/plugins/mods/reference.md#files)) | `hooks/router.ts` exports `register` | Node suite (S9.2) |
| The type files Claude Code writes for your build are the ones to trust over any page ([types](https://code.claude.com/docs/en/plugins/mods/create.md#get-the-types-for-your-build)) | The mod and its test are type-checked against those types | `sh tests/mod/typecheck.sh` and CI (S9.5) |
| `agent.spawn` takes a model and effort through `next`, and `turn.step` takes an effort ([events](https://code.claude.com/docs/en/plugins/mods/events.md)) | Sets a spawn's model and effort, and only the effort of a main-loop turn | Node suite, live (S2.1) |

## Plugin packaging

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| `engines`, `network` and `userConfig` are manifest fields ([manifest](https://code.claude.com/docs/en/plugins/manifest-reference.md#fields)) | Requires Claude Code 2.1.329 or later, names the hosts it reads, and offers its options in `/config` | `claude plugin validate --strict` (S9.3) |
| Options reach hooks as `CLAUDE_PLUGIN_OPTION_<KEY>`, and `${CLAUDE_PLUGIN_DATA}` is kept across updates ([user configuration](https://code.claude.com/docs/en/plugins/manifest-reference.md#user-configuration)) | Reads its options there and keeps its state in its data folder | Node test "plugin options reach the CLI env" (S2.8) |
| `plugin validate --strict` treats warnings as errors, and `plugin eval` runs live cases ([CLI reference](https://code.claude.com/docs/en/plugins/cli-reference.md#plugin-validate)) | CI runs both | S9.3, S9.4 |

## Skills and agents

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| Put a skill's key use first in its `description`, and `allowed-tools` grants tools without asking, it doesn't restrict them ([skills](https://code.claude.com/docs/en/skills.md#frontmatter-reference)) | Each skill's description opens with its use, and its Bash grants cover only the plugin's own command | `test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files` |
| An agent needs only `name` and `description`, and `tools` limits what it can use ([frontmatter](https://code.claude.com/docs/en/sub-agents.md#supported-frontmatter-fields)) | Seven role agents, each with a `tools` list | `test_skills_have_descriptions_and_use_the_launcher` |
| A plugin skill loads as `/plugin-name:skill-name` beside any other skill of that name, and a skill of yours named `ship`, `ship-local` or `steward` doesn't load where Claude Code's own `/ship` is available ([skills](https://code.claude.com/docs/en/skills.md#resolve-skills-that-share-a-name)) | The pipeline is `/model-router:ship`, and the README and the guide say to type it in full, since the bare `/ship` runs Claude Code's command | Not checked live; `test_a_typed_plugin_skill_is_read_from_its_plugin_root` reads `/model-router:ship` from the plugin's own folder |

## Permissions

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| A hook that returns `updatedInput` with no `permissionDecision` can be denied in auto mode ([auto mode](https://code.claude.com/docs/en/hooks.md#pretooluse-updatedinput-in-auto-mode)) | Never approves a call. The mod sets the model after the call was reviewed, and a fallback rewrite that was refused passes through untouched the next time | Live in five permission modes (S3.8, S12.15) |

## Model limits, providers and prices

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| A managed `availableModels` applies alone, otherwise the user, project and local lists are joined, and `deniedModels` counts only in managed settings ([restrict model selection](https://code.claude.com/docs/en/model-config.md#restrict-model-selection)) | Routes only to models those lists leave open | `test_a_managed_list_applies_alone_else_the_other_sources_are_concatenated` (S3.11) |
| Aliases resolve per provider, and on Bedrock `sonnet` and `haiku` can be older versions ([model aliases](https://code.claude.com/docs/en/model-config.md#model-aliases)) | Routes by alias, and on the mod path sends the family's newest ID where an alias lags, falling back to the alias | `NewestOnProviders` tests, no live provider run (S3.10) |
| A Haiku 5.5 request costs more per token when its prompt is over 100K tokens ([model configuration](https://code.claude.com/docs/en/model-config.md)) | Prices each request on its own | `LongPromptPricing` tests (S4.7) |

## Anthropic's best practices

| The docs say | Model Router | Checked by |
| :- | :- | :- |
| Set `model: haiku` on a subagent whose work is simple ([best practices](https://code.claude.com/docs/en/best-practices.md#know-what-subagents-cost)) | Gives simple briefs Haiku without a pin you have to write | `test_the_shipped_ladder_picks_at_balanced_priority` |
| Give Claude a check to run, and enforce it with a Stop hook ([best practices](https://code.claude.com/docs/en/best-practices.md#give-claude-a-check-to-run)) | The verify gate, on by default, asks once for a test run before a turn ends when code changed and nothing checked it | `Gates` tests (S2.23) |

## Staying current

The docs change often, so the plugin reads them again every day. At session start, at most once in 24 hours, it fetches the Claude Code changelog, the docs index (`llms.txt`), the model configuration page and the model deprecations page, plus the Models API when an Anthropic API key is set (`scripts/modelrouter/upstream.py`). `/model-router:refresh` shows what changed that affects routing, and CI runs the same check every day (`.github/workflows/upstream-watch.yml`). The plugin's own `docs-researcher` agent checks a feature against the current docs and changelog, not memory.

## Where it goes past the docs

These are the places the plugin relies on something the docs don't state, does something other than they advise, or goes further than they do. Each is recorded in [TRACEABILITY.md](TRACEABILITY.md) or [RELEASE_READINESS.md](RELEASE_READINESS.md).

- It picks a model for every agent that names none. The docs' default is the conversation's model. That is the plugin's job, and your own choices still come first.
- The platform docs call effort often a better lever than switching models, and suggest sweeping effort before adding a second model ([effort](https://platform.claude.com/docs/en/build-with-claude/effort)). The router picks a model and an effort for every level at once, and for standard that is Sonnet at `medium`, which the docs put about 7 points below `high` at coding.
- Anthropic's re-run figures come from Opus 5.5 at `low`, re-run at `high` when a reliable check failed. The router starts at `medium`, its climb usually moves to a stronger model, and its trigger is Claude issuing the same call again, not a check.
- It refuses one kind of call. On the `Agent` call fallback, a repeated call that would break your `max_tier` cap is denied with the model to use instead (S2.25).
- On the fallback path, auto mode can deny a rewritten call once, as the docs warn. The call then runs untouched on the retry (S12.15).
- The mod reads `agent.spawn` fields such as `fork`, `parentModel` and `workflow` that the reference page doesn't list. They are in the type files Claude Code writes for each build, which the docs say to trust (S5.6).
- One claim rests on the binary: in the desktop app behind a Claude apps gateway, `$.session.authorize()` returns nothing. There is no desktop live run yet (S5.6, S3.7).
- `main_effort` stays off behind any `ANTHROPIC_BASE_URL`, which is stricter than the docs: they say caching behind a gateway depends on the gateway.
- An `availableModels` list passed with `--settings` is joined with the others. The docs' merge rule names user, project and local settings only.
- Its hooks match `Agent|Task`. The docs name only `Agent`, and `Task` is the tool's older name.
- Bedrock, Google Cloud's Agent Platform and Microsoft Foundry have no live run yet (S3.10), and fast mode requests are priced at standard rates (S4.8).
- The docs don't say whether `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` wins over the model a mod sets. A live run on 2.1.331 showed it does, and the router follows that.
- The docs give no effort order for workflow agents, so the router treats them as subagents. The mods reference names subagents and teammates for `agent.spawn`, and the type files and a live run show it fires for workflow agents too.
- A script's `effort` on `agent()` isn't on the spawn event, so on a stage that names no model the router sets its own effort over it.
