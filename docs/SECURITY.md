<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="GLOSSARY.md">Glossary</a> &nbsp;&middot;&nbsp; Next: <a href="PERFORMANCE.md">Performance</a></p>
<!-- End of page head. -->

# Security

Python names are in `plugins/model-router/scripts/modelrouter/`, and Node tests in `tests/mod/router.test.ts`.

## Reporting a vulnerability

Report it privately through the repository's GitHub security advisory form ([Report a vulnerability](https://github.com/crisnahine/claude-code-model-router/security/advisories/new)). Put no details in a public issue.

## What it stores

Everything goes in one data folder: `CLAUDE_PLUGIN_DATA`, else `~/.claude/plugins/data/model-router-local`, with `CLAUDE_CONFIG_DIR` in place of `~/.claude` when set (`store.data_dir`). On POSIX the folder is mode 0700 and each file the Python runtime writes is created 0600 (`store._private_file`).

- `ledger.jsonl`: session and agent ids, agent names, levels, reasons, model names, token counts and Claude Code's dollar estimate. Rows older than 30 days go, and past 4 MiB the older half goes (`store.append_ledger`).
- `routes.json`, `done.json`, `launched.json`, `pending.json`: where each call went and what came back. A call is known by a hash of its agent and prompt (`calls.identify`), its task by a hash of its agent and description (`calls.task`). No brief, prompt or description is stored (`SecurityClaims.test_no_brief_or_description_is_stored`).
- `switches.json`: per session, the `/model-router` switch, whether the session check ran, and the model family and effort it found (`settings.switch`, `session.check`). Dropped at session end (`settings.forget`) or after a day unwritten.
- `sessions.json`, `edits.json`, `allowlist.json` (the model settings the mod read, and the project path), `state.json` (model health), `locks.json`, a `.flock` file per state file, empty marks under `mod-calls/`, and up to three `.corrupt-<time>` copies of a file that failed to parse.

Per-session files keep their 200 most recently written sessions (`store.KEEP_ENTRIES`), and `routes.json`, `done.json` and `launched.json` drop a session after a day with no write (`calls.KEPT_FOR`).

Nothing is written outside that folder (`SecurityClaims.test_it_writes_only_under_its_data_directory`) except Python's own bytecode. Most Pythons write `.pyc` files into the plugin's `scripts/modelrouter/__pycache__`. One that writes none under the launcher's `-I`, as Apple's Python 3.9 does, gets a cache in `~/.cache/model-router/pycache`, used only while that folder and its parent are real folders owned by you, and set to mode 0700 (`private_cache` in `scripts/model-router.py`, `Launcher.test_the_bytecode_cache_is_a_folder_only_this_user_can_write`). Its path comes from the password database, never from `HOME`, which a repository's settings can set for a hook.

A read-modify-write waits at most one second for its lock, then skips that one write and counts it in `locks.json` (`store.locked`). A shared file a newer release wrote is never written (`store.try_update`). A hand-edited state file can change routing and the reports, but can't run code or pass the cap, since every call is clamped again (`Registry.clamp`).

## Network

The plugin opens no connection. No shipped Python module imports `urllib`, `http`, `socket` or `ssl`, `hooks/router.ts` has no `$.http` or `fetch(` call, and `plugin.json` declares no `network` hosts (`SecurityClaims.test_nothing_shipped_opens_a_connection`). The agents use Claude Code's tools under your permission rules: `architect` has `WebFetch`, and `implementer` lists no `tools`, so it gets every tool the session has.

## What it reads, and whom it trusts

The Python runtime starts no process and runs no code from the repository. It reads hook and mod payloads, its own state, `config/models.json`, the user's and a project's `model-router.json`, agent frontmatter (`name`, `model`, `effort`, `tools` and `disallowedTools`) and Claude Code's plugin install records (`agents.find`), the tail of the session's and a subagent's transcript (`session.transcript_tail`), and the plugin's options and model variables from the environment (`settings.resolve`).

It reads no settings file. The mod asks Claude Code for each settings source and passes on only `availableModels`, `deniedModels`, `availableModelsMatch`, `modelOverrides`, `maxEffortLevel`, `effortLevel` and each `modelSettings` entry's `maxEffortLevel` and `effortLevel`, never `env` (`KEYS` and `effortFields` in `hooks/router.ts`; Node test "mod-start and mod-spawn carry the allowlist and effort keys of each settings source, and only the effort fields of modelSettings").

**The user's file and options.** Its `models`, `levels` and `agents` apply as written. `routing_mode` comes only from `/config`, and a `routing_mode` in either file is dropped (`config._clean`).

**A project's `.claude/model-router.json`.** The file read is the nearest one from `CLAUDE_PROJECT_DIR`, else the hook's `cwd`, up to the repository root, never the home folder, and only the start folder's when the root, its `.git` or its `.claude` belongs to another user (`config._up_to_root`, `config._mine`; `ProjectFromASubfolder.test_the_walk_stops_where_the_root_or_its_git_or_claude_folder_is_not_mine`, `ProjectFromASubfolder.test_the_walk_stops_where_the_roots_git_or_claude_folder_is_another_users`). On Windows the folder's owner SID must be the process's user or the owner its new files get, which is Administrators for an elevated admin (`Owner.test_a_folder_and_file_this_process_made_are_mine`, `Owner.test_the_windows_folder_is_not_mine`). A repository created from an elevated prompt and opened from a normal one is owned by Administrators, so there only the start folder's file is read.

- Only `models`, `levels` and `agents` are read, never `prices`, `newest` or `older_aliases` (`SecurityClaims.test_a_repository_file_keeps_only_models_levels_and_agents`). A key that isn't a short plain name is dropped with a note that doesn't repeat it (`Registry.test_a_project_files_model_or_agent_key_that_is_not_a_plain_name_is_dropped_unechoed`).
- An added model whose name overlaps a shipped or user model is dropped. The router never picks an added model and never counts one under a cap: it runs only when a call, an agent's own `model` or the user's rule names it (`SecurityClaims.test_a_project_model_is_never_the_routers_pick`, `test_a_project_model_cannot_take_a_trusted_name_or_count_under_a_cap`).
- Levels and agent rules only raise: a lower `needs` or `effort` is dropped, `level` is a floor, `model` applies only when it isn't weaker than what the call would run on, and `route: false` is ignored. A user rule that sets the agent's level or model holds the project's (`config._raises_only`, `config._rules`). `ProjectFileNeverRunsACallWeaker.test_no_project_file_runs_any_call_weaker` walks each kind of project file against every call shape and finds none that runs a call weaker.
- `/model-router:models` lists what was held. A rule's `model` that is not a short plain name is dropped unshown, and an agent file's `model` outside the registry is shown only when it is a plain name, so a repository can't put text of its choosing into output Claude reads (`Registry.test_a_project_rules_model_that_is_not_a_plain_name_is_dropped_unechoed`, `Registry.test_an_agent_files_model_that_is_not_a_plain_name_is_never_echoed`).
- With no cap set, a raise can cost more. `max_tier` caps the model and leaves the effort alone.

**A project's `.claude/settings.json`.** Claude Code joins the non-managed `availableModels` lists, so a repository can add to the user's. It can't widen a managed list or `deniedModels`, or raise `maxEffortLevel`, since the lowest applies ([model config](https://code.claude.com/docs/en/model-config.md), [settings](https://code.claude.com/docs/en/settings-reference.md)). Its `env` block can set `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` once you trust the folder, and the router doesn't lower a model those set, so a cloned repository can run subagents above your `max_tier` (`Router.test_the_users_environment_settings_win`, `RepeatGuard.test_a_refused_effort_only_rewrite_under_force_passes_through`). The launcher runs Python with `-I`, so a `PYTHONPATH` set there loads no code (`Launcher.test_a_repositorys_python_path_cannot_load_code_into_the_launcher`), but `sh` and `python3` are whatever `PATH` finds first, which the block can also set.

**Managed settings.** A managed `availableModels` applies alone, and `deniedModels` counts only there (`Allowlist.test_a_managed_list_applies_alone_else_the_other_sources_are_concatenated`). An allow list is cut to 200 entries of up to 200 characters, and a managed deny list over that leaves no model open (`Allowlist.test_a_deny_list_over_the_cap_leaves_no_model_open_while_an_allow_list_is_only_cut`). Claude Code still applies both lists to every subagent model ([sub-agents](https://code.claude.com/docs/en/sub-agents.md)).

**Prompt and brief text.** The router reads no brief. Claude names the model and effort, so text Claude reads can sway what it names, within the cap. A call that names no model gets its level from its agent definition's `tools` and `disallowedTools` alone: `lookup` when they leave out Edit, Write, NotebookEdit and Bash, else `standard` (`agents.read_only`; `UnnamedCalls.test_the_pick_never_reads_the_brief`). A repository's own agent file sets that level through its tool list, as its `model` field can already set the model. A `description` starting with "Redo:" makes a call climb, inside the usable models (`router._redo`; `RepeatGuard.test_a_redo_held_under_the_cap_on_its_model_takes_more_effort_up_to_max`).

## What reaches Claude's context

All of it goes to the model provider with the conversation:

- the session-start note, which names `CLAUDE_CODE_SUBAGENT_MODEL` under FORCE only when it is a short plain name (`Notes.test_an_environment_model_reaches_claude_only_as_a_plain_name`), and, where the mod runs, the same note in the Agent tool's description and its form for a workflow script in the Workflow tool's (`tool.describe` in `hooks/router.ts`);
- a note when the user's prompt names a shipped or user model (`hooks.user_prompt`);
- the fallback's note on each call it rewrites, and its deny reason (`fallback.route`);
- a sentence after a redo that had no step left, naming only a registry model and an effort (`spawn.redo_note`);
- the Stop ask, and the notes after a model error or a mid-run model switch (`hooks.agent_failed`, `hooks.post_agent`);
- skill output: the policy and `explain` from `/model-router:route`, model status, state file paths, held rules and a workflow pin named only as a plain name from `/model-router:models` (`Notes.test_the_models_report_names_a_workflow_pin_only_as_a_plain_name`), and agent and model names, tokens and dollar estimates from `/model-router:usage`.

Notices about pinned models go to `systemMessage`, shown to the user and kept out of Claude's context ([hooks](https://code.claude.com/docs/en/hooks.md#json-output)).

## The one denial

It never approves a tool call (`SecurityClaims.test_it_never_approves_a_tool_call`). It denies one thing, on the settings-hook path only: a call it rewrote to a lower model that comes back five or more seconds later with no result or error in between, when the model the call would run on untouched is above `max_tier`. The denial names the model to use (`fallback._guard`; `RepeatGuard.test_the_cap_holds_even_when_a_lowering_rewrite_did_not_take`). The mod denies nothing. With no answer from the runtime and routing on, it lowers a named model above `max_tier` to the cap (`capped` in `hooks/router.ts`).

## The mod

`hooks/router.ts` runs `sh <plugin root>/scripts/model-router.sh <command>` as an argument array, so no shell parses the input, and sends the event as JSON on stdin with an 8-second timeout (`ask`). Every hook passes the event on unchanged when anything fails (Node test "every hook falls back to next(e) when it throws"). It never changes a prompt. Its only file writes are empty marks under `mod-calls/`: one per `Agent` call, and `<tool_use_id>.moved` for a spawn a later mod moved. Each is named by the call's `tool_use_id` only when that id is 1 to 128 letters, digits, `_` or `-` (`PLAIN_ID`), and the settings hooks check the same pattern before they remove one (`Mod.test_only_a_plain_tool_use_id_is_looked_up`, `MovedByAnotherMod.test_only_a_plain_tool_use_id_is_looked_up`).

## What the mod runs on your choice

Only in a session with a person at the prompt (`isInteractive`), never in `claude -p` or the Agent SDK (Node test "a headless run registers, draws and asks nothing, and routes as before"):

- **`/model` and `/effort`.** The session check runs Claude Code's own commands through `$.command.run` only after you pick a setup in its dialog, and only for a label it sent: a dismissal or typed text runs nothing (Node test "a dismissal or free text at either step changes nothing"). The arguments come from the runtime: a registry family alias or its newest ID, and an effort level (`session._model_choice`). Claude Code then saves the model in your user settings and the effort under `modelSettings`, as it does for a typed `/model` or `/effort`, and applies its own allowlist and organization rules. A refused `/model` stops the switch before `/effort` (Node test "a switch that fails says so and stops, and one Claude Code did not take says the session stayed").
- **One option.** The mod writes `session_check` through `$.config.set`, from `/model-router check on` or `off` or the check's "Don't ask again", and never when `$.config.list()` says managed settings own it (Node test "/model-router check off and on write the option, unless managed settings own it"). It never writes `routing_mode`: `/model-router on` and `off` keep the switch in `switches.json` for that session only.

## Skills

Each skill's `allowed-tools` pre-approves only the launcher commands that skill runs, behind the fixed `--data-dir "${CLAUDE_PLUGIN_DATA}"` prefix, and only `models` may run `models reset`, the one skill command that writes state (`SkillGrants.test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files`). `usage` and `models` pass `$ARGUMENTS` unquoted, and both set `disable-model-invocation: true`, so only text the user typed reaches them. The `route` and `models` lines pass the options as `${user_config.<option>}`, which Claude Code fills from user or managed settings, never a repository's ([plugin configs](https://code.claude.com/docs/en/settings-reference.md#pluginconfigs)).
