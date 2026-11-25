# Rule files and the trust model for a project's file

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.3.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

## What we built

- `settings.resolve` (`plugins/model-router/scripts/modelrouter/settings.py`) reads the eight `userConfig` options from `CLAUDE_PLUGIN_OPTION_<KEY>`. Skill commands run through Bash, which doesn't get those variables, so `resolve(recorded=True)` falls back to the copy `settings.record` saved at SessionStart.
- `config.load` (`config.py`) reads three files: the shipped `config/models.json`, the user's `~/.claude/model-router.json` (`store.claude_home`, which honors `CLAUDE_CONFIG_DIR`) and `<project>/.claude/model-router.json`. `config.project_dir` takes the folder from the hook input's `cwd` first, then `CLAUDE_PROJECT_DIR`.
- `config._clean` keeps only well-formed `routing_mode`, `models`, `levels` and `agents`. `config._prices` takes prices from the user's file only.
- `config.build` trusts the user's file in full. For the project's file: `_raises_only` drops a level `needs` or `effort` below the shipped or user value; `_rules` turns a rule's `level` into `Rule.floor` and its `model` into `Rule.suggested`, and ignores `route: false`; a project model whose name overlaps a trusted one is dropped, and the rest are marked `Model.project`, which `Registry.select`, `Registry.clamp`, `Registry.available`, `calls.strongest` and `router.decide` never treat as trusted.
- `routing_mode` from either file can only turn routing down: `settings.resolve` takes the weakest of the option and the files.
- `max_tier` is a plugin option only, so no file can pass it. `Registry.held` and `Registry.problems` feed `/model-router:models`.
- Docs that state the trust rule: README.md line 140, docs/GUIDE.md "Configuration", docs/ARCHITECTURE.md "Trust", docs/SECURITY.md "Who can influence routing", GLOSSARY.md "Override file".

## What the sources say

Plugin options (`userConfig`)

- "`userConfig` declares values Claude Code prompts the user for when the plugin is enabled, so users don't edit `settings.json` themselves." Field types are "One of `string`, `number`, `boolean`, `directory`, or `file`", plus `multiple` for "an array of strings". https://code.claude.com/docs/en/plugins/manifest-reference (section "User configuration")
- "Non-sensitive values are saved under `pluginConfigs` in the user's `settings.json`. Sensitive values go to the platform's secure credential store instead." Same page, "Where values are stored".
- Values reach the plugin as "`CLAUDE_PLUGIN_OPTION_<KEY>`: exported to hook processes for every option", and as "`${user_config.KEY}`: substituted in MCP server config, LSP server config, exec-form hook `args`, and skill and agent content". Same page, "Reference a saved value".
- Mods get them too: "`options` holds the values of the `userConfig` fields the manifest declares, with defaults filled in." https://code.claude.com/docs/en/plugins/mods/reference (section "Files")
- `pluginConfigs` scope is "User or managed", and: "Claude Code ignores project and local entries because it substitutes these values into plugin hook, MCP, and LSP configurations, and a cloned repository must not be able to supply them." https://code.claude.com/docs/en/settings-reference#pluginconfigs
- The changelog for 2.1.207: "Plugin option values (`pluginConfigs`) are no longer read from project-level `.claude/settings.json`; only user, `--settings`, and managed settings are honored". https://code.claude.com/docs/en/changelog
- Type files for this build (`plugins/model-router/.claude-plugin/types/claude-code/index.d.ts`, the generated mods API types): a `/config` row has `isLocked`, "Whether a trusted source (managed settings, the organization's policy) owns the value, so the menu shows it and refuses a change."
- `CLAUDE_PLUGIN_DATA` is "kept across plugin updates", but "By default, Claude Code deletes the `${CLAUDE_PLUGIN_DATA}` directory when you uninstall the plugin from the last place it's installed." https://code.claude.com/docs/en/plugins/manifest-reference (section "Environment variables")

Settings precedence and managed settings

- "Settings precedence, highest first: managed settings, command line, project local, shared project, user." https://code.claude.com/docs/en/settings#settings-precedence
- "Nothing in your own settings files or `--settings` overrides a managed key". Same section.
- Keys scoped `User or managed`, `User` or `Managed` "never apply from the shared file, apart from a few that a repository file can still switch off." https://code.claude.com/docs/en/settings (section "Troubleshoot a setting that doesn't apply")
- `$.settings.read` reads one source of `user`, `project`, `local`, `flag` or `policy`. https://code.claude.com/docs/en/plugins/mods/api (section "Read all settings, one source, or one key")

What a repository may set in Claude Code itself

- `model`, `effortLevel`, `modelSettings`, `availableModels`, `maxEffortLevel` and `agent` all have scope "Any file". https://code.claude.com/docs/en/settings-reference (entries `model`, `effortLevel`, `modelSettings`, `availableModels`, `maxEffortLevel`, `agent`)
- `maxEffortLevel`: "When several scopes set a cap, the lowest applies, so a cap set in one scope can't be raised from another". https://code.claude.com/docs/en/settings-reference#maxeffortlevel
- Non-managed `availableModels` lists merge: "Across non-managed scopes Claude Code merges the arrays as usual." https://code.claude.com/docs/en/settings (section "Lists merge instead of overriding")
- `env` from project settings applies "after you trust the workspace, or at startup in `-p` mode", but "Variables Claude Code classifies as safe, such as model selection, timeouts and limits, and feature toggles: at startup from every settings file". https://code.claude.com/docs/en/settings-reference#env
- Project subagents (`.claude/agents/`) have priority 3, above user subagents at 4: "When multiple subagents share the same name, Claude Code uses the one from the higher-priority location." https://code.claude.com/docs/en/sub-agents#choose-the-subagent-scope

The documented trust model for repository files

- "`permissions.allow` rules and `permissions.additionalDirectories` entries in a project's `.claude/settings.json` grant capability, so Claude Code applies them only after you accept the workspace trust dialog for that folder. ... `deny` and `ask` rules aren't affected, since they only restrict." https://code.claude.com/docs/en/permissions#project-allow-rules-and-workspace-trust
- The table "What runs before you trust a folder": settings hooks, the `env` block and helper commands are "Used" even when only a parent was trusted or in `-p`; frontmatter hooks in a project subagent and inline `mcpServers` in a project subagent are "Not used". https://code.claude.com/docs/en/permissions (section "What runs before you trust a folder")
- One-way keys exist: `permissions.blockReadsOutsideWorkingDirectories` is "Any file. A `true` in any file applies, so a repository can turn the block on for itself but can't lift yours." https://code.claude.com/docs/en/settings-reference#permissions-blockreadsoutsideworkingdirectories
- Project `env` can't set "variables that a checked-out repository shouldn't control", and the list names storage paths, telemetry export, startup and sign-in variables. Model variables are not on it. https://code.claude.com/docs/en/settings-reference (section "Variables Claude Code ignores in `env`")

Where a hook finds the project

- "`${CLAUDE_PROJECT_DIR}`: the project root where the session started." And: "`cwd` follows Claude: the `cwd` field in the hook's input JSON is the worktree root after Claude enters a worktree, and the new directory after Claude runs `cd`." https://code.claude.com/docs/en/hooks (section "Reference scripts by path")

The Agent tool's model field

- The generated tool types list `model?: "sonnet" | "opus" | "haiku" | "fable"` for the Agent tool (`plugins/model-router/.claude-plugin/types/claude-code-tools/index.d.ts`, the mods API type files). These are the four shipped models.

## Verdict

Partly right.

Plugin options through `userConfig` and `CLAUDE_PLUGIN_OPTION_<KEY>`, a cap (`max_tier`) that no file can pass, and prices only from the user's file all match the documented rule that a cloned repository must not supply plugin options (settings-reference `pluginConfigs`, changelog 2.1.207). A project file that only raises is a plugin choice the docs neither ask for nor forbid; it is the same one-way shape as `blockReadsOutsideWorkingDirectories` and `maxEffortLevel`, and it suits a quality-first goal. But the promise around it is wrong. Claude Code lets a repository lower subagent models and effort through `.claude/settings.json` (`maxEffortLevel`, `availableModels`, model `env` that applies before trust) and through `.claude/agents/` pins, and the router honors all of those, so "a cloned repository can make its subagents cost more, never run weaker" is false. Two parts of the project file also do no work or cut across documented rules: project-added models can never be named on an Agent call, and `routing_mode` in a file can turn down a value managed settings locked.

## What to change

1. **rule-files-1. Correct the "never run weaker" claim (required by a source).** Rewrite the trust text in README.md (line 140), docs/GUIDE.md "Configuration", docs/ARCHITECTURE.md "Trust" and the per-agent rule step, docs/SECURITY.md "Who can influence routing", GLOSSARY.md "Override file", and the `config.build` and `registry.Rule` docstrings. Say that the project's `model-router.json` only raises, and that the repository's own `.claude/settings.json` (`maxEffortLevel`, `availableModels`, `env` with `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` or `CLAUDE_CODE_SUBAGENT_EFFORT`) and its `.claude/agents/*.md` `model` and `effort` can lower, and the router follows them as Claude Code does. Deletes nothing in code. Sources: https://code.claude.com/docs/en/settings-reference#maxeffortlevel, https://code.claude.com/docs/en/settings-reference#env, https://code.claude.com/docs/en/sub-agents#choose-the-subagent-scope.
2. **rule-files-2. Delete the project file's `models` block (judgment, backed by the type files).** The Agent tool's `model` takes only `sonnet`, `opus`, `haiku` or `fable`, all shipped, and the router never picks a project model, so a model a project adds can only run when the user's own rule names it, which the user can do in their own file. Touches `config._clean` (keep `models` for the user's file only), `config.build` (`vouched`, `added`, the overlap check, the `added` argument to `_models`), `config._models`, `registry.Model.project` and `Model.trusted`, the trusted filters in `Registry.select`, `Registry.clamp`, `Registry.available` and the redo step, `calls.strongest`, the untrusted branch in `router.decide`, `report.py` (the ranking and cap lines that read `.project`), `Held` of kind `model`, and their tests. Source: `plugins/model-router/.claude-plugin/types/claude-code-tools/index.d.ts`.
3. **rule-files-3. Drop `routing_mode` from both override files (judgment, backed by settings precedence).** It already exists as a plugin option in `/config`. In a file it can turn routing to `advise` or `off` under a value managed settings set and locked, while the docs say "Nothing in your own settings files or `--settings` overrides a managed key" and Claude Code reads plugin options only from user, `--settings` and managed settings. Touches `config._clean`, `config.build` (`file_modes`), `Registry.routing_mode`, `settings.resolve` (`Settings.mode` becomes `routing_mode`), the bench and tests that set it in a file. Sources: https://code.claude.com/docs/en/settings#settings-precedence, https://code.claude.com/docs/en/settings-reference#pluginconfigs.
4. **rule-files-4. Read the project file from the session's project root (judgment).** `config.project_dir` prefers the hook's `cwd`, which the docs say moves after Claude runs `cd` or enters a worktree, so a nested clone's `.claude/model-router.json` can apply mid-session and the root's can drop out. For `config.load`, use `CLAUDE_PROJECT_DIR` ("the project root where the session started"), or the git root of `cwd` as `allowlist._repository_root` already finds it. Leave `agents.find` on `cwd`, since project subagents are found by walking up from the working directory. Deletes nothing. Source: https://code.claude.com/docs/en/hooks (section "Reference scripts by path").
5. **rule-files-5. Give skill commands the options through `${user_config.KEY}` (judgment).** The docs substitute non-sensitive option values into skill content, so a skill can pass them to the CLI on its command line instead of `settings.resolve(recorded=True)` reading what SessionStart saved, which can be stale after a `/config` change mid-session. Touches the skill files that run the CLI, `settings.resolve` (the `recorded` path) and `settings.record`. Deletes the recorded copy of the options in state. Source: https://code.claude.com/docs/en/plugins/manifest-reference (section "Reference a saved value").
6. **rule-files-6. Keep only `level` in a project rule (judgment, smaller option).** A floor already raises an agent. `Rule.suggested` adds a second strength check in `router.decide` that only moves a call to a model not weaker than it would get anyway. Touches `config._rules`, `registry.Rule.suggested`, the `suggested` block in `router.decide`, the ledger's `held` text for it, and tests. Deletes that block. No source requires it.

Item 1 is required by the sources. Items 2 to 6 are judgment.

## What the sources do not say

- Where a plugin should keep user-written config that `userConfig` types can't hold, such as nested maps of models, levels and agent rules: not documented. Nothing names `~/.claude/<plugin>.json` as a place for plugins, and `CLAUDE_PLUGIN_DATA` is deleted on uninstall, so it is not a fit for a file the user writes.
- Whether a plugin should read a config file from the project at all: not documented. The docs give plugins no project-scope config: `pluginConfigs` ignores project and local entries.
- Whether a plugin may add its own top-level key to `settings.json`, and whether `$.settings.read` returns it: not documented.
- Whether workspace trust should gate a plugin's own project file, and how a plugin could read trust: not documented. `projects["<path>"].hasTrustDialogAccepted` in `~/.claude.json` is documented only as a key you set by hand.
- Whether managed settings can set or lock anything in a plugin's own files: not documented. Only `pluginConfigs` is managed.
- Whether a repository raising every subagent to a costly model counts as a risk: not documented. Claude Code lets a project set `model` and `effortLevel` from any file with no trust step named for them.
- Whether `CLAUDE_CODE_SUBAGENT_EFFORT` counts as a "safe" variable that a project `env` applies before trust: not documented by name.
- Which folder's `.claude/settings.json` applies after a Bash `cd`, as opposed to `/cd`: not documented.
- Whether "only raises" is the right direction for routing: not documented. The one-way keys in Claude Code point toward restriction for safety, and for model and effort Claude Code lets a repository move either way.
