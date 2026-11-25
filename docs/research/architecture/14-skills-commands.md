# The plugin's skills and commands

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.2.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

Researched 2026-11-24 against Claude Code 2.1.331 (`claude --version`). Every page was fetched that day as `.md` from `code.claude.com/docs/en/`. The mod type files cited are the ones in the main checkout, `plugins/model-router/.claude-plugin/types/claude-code/index.d.ts`, headed "Written by Claude Code 2.1.330"; the `redo-fixes` worktree has no `types/` folder.

## What we built

- Five skills under `plugins/model-router/skills/`, no `commands/` folder. Each runs the Python CLI through `scripts/model-router.sh` with `--data-dir "${CLAUDE_PLUGIN_DATA}"`, injected with `` !`...` `` and pre-approved by a matching `allowed-tools` Bash rule.
- `route`: the only skill Claude can call on its own. It injects `policy` (`report.policy`), tells Claude how to run `explain` (`report.explain`, a dry run of `spawn.decide`), and carries the delegation guide: the ambiguity, scope and risk table, "complete brief", the `Redo:` marker.
- `models` (`disable-model-invocation: true`): injects `models` (`report.models` / `models_data`), and for `--json` or `reset` asks Claude to run the CLI a second time. `reset` clears `state.json` and `learned.json` (`cli._reset_health`, `_reset_feedback`).
- `usage` (`disable-model-invocation: true`): injects `usage $ARGUMENTS` (`report.usage` / `usage_data`): per-agent calls, levels, models run on, tokens, and spend at registry list prices.
- `refresh` (`disable-model-invocation: true`): injects `refresh --force` (`upstream.refresh`, `report.status`), sends the list to the `docs-researcher` agent, then runs `ack`. It has a section for when the working directory is this plugin's own repo.
- `ship` (`disable-model-invocation: true`): a prompt-only pipeline (understand, plan, build, verify, review, commit) over the `architect`, `implementer`, `verifier`, `reviewer` and `deep-solver` agents.
- The CLI (`cli.main`) builds `settings.Snapshot(None, recorded=True)`: skill commands read the plugin options that the last SessionStart saved (`settings.record` in `hooks.session_start`), because the Bash tool does not carry `CLAUDE_PLUGIN_OPTION_*`.

## What the sources say

Skills, commands, and which format to use

- "Custom commands have been merged into skills. Your existing `.claude/commands/` files keep working." https://code.claude.com/docs/en/skills.md
- In a plugin: "Commands are the older format, and skills supersede them for new work. A skill runs by name the same way, and it can also carry supporting files in its directory." https://code.claude.com/docs/en/plugins/components.md
- A plugin skill loads "as `/plugin-name:skill-name`" and, beside a same-named skill elsewhere, "Both load, because plugin skills are namespaced". https://code.claude.com/docs/en/skills.md (table "Resolve skills that share a name")
- "The bare `/fancy` also invokes the skill unless another command already uses that name." https://code.claude.com/docs/en/skills.md (section "How a skill gets its command name")
- "A skill or command of yours named `ship`, `ship-local`, or `steward`": "Where `/ship` is available, the bundled `/ship` or `/steward`. Claude Code doesn't load yours." https://code.claude.com/docs/en/skills.md. The troubleshooting page says the `Not loaded: a custom /ship` notice "is about a custom skill or command" and to "Rename it". https://code.claude.com/docs/en/ship-command-troubleshooting.md
- The bundled `/ship` "opens a GitHub pull request for your change", reviews it with `/code-review` and then watches the pull request. https://code.claude.com/docs/en/ship-command.md

What the listing costs

- "Every session where a plugin is enabled includes the names and descriptions of its skills, agents, and commands in Claude's context, and those tokens count against the user's usage whether or not the plugin gets used." https://code.claude.com/docs/en/plugins/measure.md
- The skills page is more exact. For `disable-model-invocation: true`: "Description not in context, full skill loads when invoked." For the default: "Description in context, full skill loads when invoked." https://code.claude.com/docs/en/skills.md (section "Control who invokes a skill"). The same rule is on https://code.claude.com/docs/en/features-overview.md: "Set `disable-model-invocation: true` in a skill's frontmatter to keep its description out of Claude's context."
- The listing budget "scales at 1% of the model's context window", and each entry's `description` plus `when_to_use` "is truncated at 1,536 characters". https://code.claude.com/docs/en/skills.md
- Once invoked, "the rendered `SKILL.md` content enters the conversation as a single message and stays there across later turns", and "every line is a recurring token cost". https://code.claude.com/docs/en/skills.md (sections "Skill content lifecycle" and "Types of skill content")
- After compaction Claude Code keeps "the first 5,000 tokens of each" invoked skill. https://code.claude.com/docs/en/skills.md
- To measure: `claude plugin details <name>` prints "Always-on" and per-component costs; "The always-on figure counts each component's name plus its `description` and `when_to_use` frontmatter." https://code.claude.com/docs/en/plugins/measure.md

Model invocation against user invocation

- "`disable-model-invocation: true`: Claude can't invoke the skill on its own. Use this for workflows with side effects or that you want to control timing." https://code.claude.com/docs/en/skills.md
- "`user-invocable: false`: Only Claude can invoke the skill. Use this for background knowledge that isn't actionable as a command." https://code.claude.com/docs/en/skills.md
- "If a rule must hold every time ... add it to the plugin as a hook rather than a skill." https://code.claude.com/docs/en/plugins/components.md. And under "Claude stops following a skill": "move the rule into a hook". https://code.claude.com/docs/en/skills.md

Arguments

- "`$ARGUMENTS`: All arguments passed when invoking the skill. When no placeholder receives an argument, Claude Code appends them as `ARGUMENTS: <value>`." https://code.claude.com/docs/en/skills.md
- `$N`, `$ARGUMENTS[N]` and named `arguments` exist; indexed arguments "use shell-style quoting". https://code.claude.com/docs/en/skills.md

Running scripts, `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA`

- In a plugin skill, "Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` in the same two places", the content and "Bash rules in the `allowed-tools` frontmatter". "Using the same variable in both places lets a skill run a bundled script without a permission prompt." https://code.claude.com/docs/en/skills.md
- "The variables aren't present in the environment of commands Claude runs through the Bash tool, in the main session or in a subagent. In skill, command, and agent content, write the `${...}` reference in the Markdown body instead." https://code.claude.com/docs/en/plugins/manifest-reference.md
- `${CLAUDE_PLUGIN_ROOT}` "changes when the plugin updates, so don't write state there"; `${CLAUDE_PLUGIN_DATA}` is "kept across plugin updates". https://code.claude.com/docs/en/plugins/manifest-reference.md
- Option values: "`${user_config.KEY}`: substituted in MCP server config, LSP server config, exec-form hook `args`, and skill and agent content. In skill and agent content, only non-sensitive values are substituted." `CLAUDE_PLUGIN_OPTION_<KEY>` is "exported to hook processes". https://code.claude.com/docs/en/plugins/manifest-reference.md (section "Reference a saved value")
- `pluginConfigs` is read from user or managed settings only; "Claude Code ignores project and local entries". https://code.claude.com/docs/en/settings-reference.md
- `bin/` at the plugin root is on "the `PATH` of the Bash tool's shell while the plugin is enabled". https://code.claude.com/docs/en/plugins/components.md

Injected commands

- "Injected commands never prompt for permission while the skill renders." Outside auto mode, a command whose check "returns anything other than allow" aborts the invocation; `allowed-tools` pre-approves it. https://code.claude.com/docs/en/skills.md
- "A failed command aborts the entire skill invocation ... Claude never sees the skill content for that invocation. ... The error message includes the command's output." "Any non-zero exit code counts as a failure." https://code.claude.com/docs/en/skills.md
- Each command runs under "the Bash tool's default 2-minute timeout". https://code.claude.com/docs/en/skills.md
- "`Bash(ls *)` matches `ls`", so a trailing ` *` also matches the bare command. https://code.claude.com/docs/en/permissions.md
- With a compound command, "Every subcommand must be allowed for a compound command to run without a prompt." https://code.claude.com/docs/en/permissions.md

What Claude Code already shows

- `/usage`: "Show session cost, plan usage limits, and activity stats", with "`/cost` and `/stats` are aliases". https://code.claude.com/docs/en/commands.md
- `/usage` Session block: "Claude Code computes the dollar figure locally from token counts at list price, unless a `modelPricing` table is in effect", lists "Usage by model", applies the "1.1× data residency rate", and resets on `/clear`. https://code.claude.com/docs/en/costs.md
- `/usage` plan breakdown: "recent usage attributed to skills, subagents, plugins, and individual MCP servers, each shown as a percentage of the total", over 24 hours or 7 days. https://code.claude.com/docs/en/costs.md
- `/model`: "Switch the AI model and save it as your default". The picker grays out a model the organization can't use, "with a note on why". https://code.claude.com/docs/en/commands.md, https://code.claude.com/docs/en/model-config.md
- `/agents`: "Print a reminder to ask Claude to create or manage subagents". It no longer lists running subagents. https://code.claude.com/docs/en/commands.md, https://code.claude.com/docs/en/sub-agents.md
- `/skill-doctor`, `/doctor` and `/context` report what each skill costs in context. https://code.claude.com/docs/en/skills.md
- OpenTelemetry spans carry `model` and `agent_id` per request, if the user runs a collector. https://code.claude.com/docs/en/monitoring-usage.md
- None of these shows the level a router picked, the model each subagent call ran on over several days, or a router's learned state. Not documented anywhere.

A mod command, the other way to show a table

- A mod registers a command with `$.command.register` and answers `command.run` with `{ text }`; "The hook never calls `next`, because the command has no behavior other than yours." `{ text, isTextDisplayOnly: true }` prints it "without Claude reading it" (v2.1.326 or later). `$.command.register` "throws for a taken name". https://code.claude.com/docs/en/plugins/mods/api.md
- The type files: "A hook's own answer without `next` runs no command: its `text` is shown as the command's output", and `CommandSpec.name` is run "as `/<name>`", with no plugin prefix stated (`CommandRunResult`, `CommandSpec`, mod type files 2.1.330).
- The built-in guard does not list `command.run` or `session.start` among the user-mod hooks it stops. https://code.claude.com/docs/en/plugins/mods/admin.md

Splitting and placement

- To lower the always-on figure: "Shorten skill and agent descriptions" and "Split a large plugin so users install only the components they need." https://code.claude.com/docs/en/plugins/measure.md
- Project skills in `.claude/skills/` load for "Sessions in this repository". https://code.claude.com/docs/en/skills.md
- Workflows: "A workflow moves the plan into code. With subagents, skills, and agent teams, Claude ... decides turn by turn what to spawn", and every result goes into a context window. https://code.claude.com/docs/en/workflows.md

## Verdict

Partly right.

Shipping these as plugin skills and not commands follows the docs, and so do `disable-model-invocation` on the four user-run skills, `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` in both the body and `allowed-tools`, and state kept in the data folder. Only `route` sits in the listing, at 197 characters. Five things are off, though.

- The CLI reads a copy of the options saved at session start, when skill content can take live values through `${user_config.KEY}`.
- `models` and `usage` have Claude re-type JSON and tables that the CLI already printed.
- `route` holds delegation rules that matter on every call, but Claude only loads it when someone asks about models. The docs say a rule that must hold every time belongs in a hook.
- `usage` puts a second dollar figure next to `/usage`, and the two can disagree.
- `ship` and `refresh` are outside routing. `ship` shares its name with the bundled `/ship` and only works for software. `refresh` is maintainer tooling.

## What to change

1. **Pass live option values to skill commands (judgment, backed by a source).** Add the eight options to each injected command and each matching `allowed-tools` rule as `${user_config.KEY}`, placed after the subcommand so the trailing ` *` still matches. Teach `cli.main` to read them. This deletes `settings.record`, its call in `hooks.session_start`, the `recorded` fallback in `settings.resolve` and `settings.Snapshot`, and the options keys in `state.json`. It updates `test_skill_commands_read_options_recorded_at_session_start` and the `report.py` docstring. Source: https://code.claude.com/docs/en/plugins/manifest-reference.md ("Reference a saved value"). Check live first, because the docs don't say whether the substitution happens before an injected command runs (see below).
2. **Drop `--json` from the `models` and `usage` skills and keep it on the CLI (judgment).** Scripts and CI should call `model-router.sh ... --json` directly, not ask Claude to copy JSON "unchanged". In the same change, let `models` inject one command, `models $ARGUMENTS`, and have `cli.main` accept `reset` as well as `--reset`. That ends the second run. This deletes the `--json` paragraphs and the `reset` re-run steps in `skills/models/SKILL.md` and `skills/usage/SKILL.md`, and updates their `argument-hint`. Sources: https://code.claude.com/docs/en/skills.md ("Skill content lifecycle": rendered content stays in context; "Pass arguments to skills").
3. **Make `route` short and move the every-call rules into session-start text (judgment, backed by a source).** The lines that must shape every brief go into `hooks.session_start`'s auto-mode text, which already carries the `Redo:` rule: put what makes a piece hard in the brief, and keep the `description` on a redo. The skill keeps `policy`, `explain` and the "answer with level, model and signals" output. This deletes the "How to split the work" list and the signal table from `skills/route/SKILL.md`, along with "starting on the cheapest that should do the job", which goes against the quality-first goal. Rewrite whatever survives to match the level source the caller-levels research settles on. Sources: https://code.claude.com/docs/en/plugins/components.md ("If a rule must hold every time ... add it to the plugin as a hook rather than a skill"), https://code.claude.com/docs/en/skills.md ("Keep the body itself concise").
4. **Stop showing a second dollar total in `usage` (judgment, backed by a source).** `/usage` prices at `modelPricing` and the data-residency rate when they apply. `report.usage` prices at registry list prices. Drop the "Spend at list price" table from `report.usage`, keep `output_tokens` per loop and model, and end the skill with "For cost, run `/usage`." (`/cost` is only an alias). The `spend` key stays in `usage_data` if CI or the bench reads it. Source: https://code.claude.com/docs/en/costs.md ("Using the `/usage` command").
5. **Take `ship` out of this plugin (judgment).** The bundled `/ship` reserves the name, the bare `/ship` runs the built-in, and the pipeline only knows git and code, while the plugin has to serve any kind of work. Routing already applies to whatever pipeline Claude runs, so the plugin does not need its own. This deletes `skills/ship/SKILL.md` and its mentions in `README.md`, `docs/GUIDE.md`, `docs/FAQ.md` and `docs/CLAUDE_CODE_DOCS.md`. Move the ship body used as a fixture in `test_a_skill_that_takes_its_arguments_is_profiled_by_them` into the test. Whether the role agents it calls stay is the agents decision. If it stays, rename it at least, or ship it as a separate plugin. Sources: https://code.claude.com/docs/en/skills.md (bare names and the reserved `ship`), https://code.claude.com/docs/en/plugins/measure.md ("Split a large plugin").
6. **Move `refresh` to the repo's own `.claude/skills/` (judgment).** Its checks (frontmatter fields, hook fields, `plugin.json`, the "own repository" branch) are things only a maintainer can act on. Users already get new aliases and retiring models from `hooks.session_start` and the `models` table. This deletes `skills/refresh/SKILL.md` from the plugin, the `docs-researcher` agent and its `config/models.json` crew entry if nothing else uses them, and the `refresh`/`ack` lines in `test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files`. The CLI subcommands stay for the project skill and CI. If it stays in the plugin, make `cli.main` exit 0 on the offline path, since a non-zero exit aborts the skill before Claude sees it. Source: https://code.claude.com/docs/en/skills.md ("When an injected command fails"; project skill location).

None of these is required by a source. Every rule the docs state as a must is already met: the path variables, `allowed-tools` matching, data kept out of `CLAUDE_PLUGIN_ROOT`, and the reserved `ship` name, which the plugin prefix avoids.

Considered and not proposed: serving `models`, `usage` and `policy` as mod commands with `isTextDisplayOnly`. That would print the CLI's output exactly with no model turn. But the names carry no plugin prefix and must avoid every taken name (`/usage` is built in), and a mod can be stopped by managed settings. That would mean keeping the skills as a fallback, which grows a plugin already criticized as oversized.

## What the sources do not say

- Whether `${user_config.KEY}` is substituted inside a `` !`...` `` line before it runs, and whether it is substituted in `allowed-tools`. The docs name only "skill and agent content" for it, and name only `${CLAUDE_SKILL_DIR}`, `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` for `allowed-tools`. Not documented.
- Whether a running session picks up a value changed with `/plugin configure`, for hooks or skills. Not documented.
- Whether `$ARGUMENTS` inside an injected command is quoted or escaped. The docs only say the compound-command check applies to every subcommand. Not documented.
- How the measure page's "names and descriptions of its skills ... in Claude's context" squares with the skills page's "Description not in context" for `disable-model-invocation`. The skills page is the more specific one. Whether the name still costs anything is not documented. `claude plugin details model-router` would show it, but this research was not allowed to run it.
- Whether the `/usage` Session block's "Usage by model" counts subagent requests. Not documented.
- Whether a plugin skill named `ship` loads at all. The reserved-name rows say "of yours" and "custom", and plugin skills are not named. Not documented.
- Whether a mod command's `{ text }` answer starts a model turn. The docs and types describe it as command output with no turn, but don't say so in words. Not documented.
- Whether a mod command name gets the plugin prefix. The type files say "the person runs it as `/<name>`". Not documented further.
