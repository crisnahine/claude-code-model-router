# Model control in Claude Code: what a plugin can and can't do

Researched 2026-11-22 against Claude Code **2.1.330** (`claude --version`, binary at `~/.local/share/claude/versions/2.1.330`). Docs were fetched the same day from `code.claude.com/docs/en/*.md` and `platform.claude.com/docs/en/*.md`. Where a claim rests on the installed binary rather than a doc page, it says so. Anything not confirmed in a primary source is marked **UNCONFIRMED**.

Short names used for sources below:

- `[hooks]` https://code.claude.com/docs/en/hooks
- `[model-config]` https://code.claude.com/docs/en/model-config
- `[sub-agents]` https://code.claude.com/docs/en/sub-agents
- `[skills]` https://code.claude.com/docs/en/skills
- `[workflows]` https://code.claude.com/docs/en/workflows
- `[agent-teams]` https://code.claude.com/docs/en/agent-teams
- `[mods-ref]` https://code.claude.com/docs/en/plugins/mods/reference
- `[mods-events]` https://code.claude.com/docs/en/plugins/mods/events
- `[mods-overview]` https://code.claude.com/docs/en/plugins/mods/overview
- `[mods-admin]` https://code.claude.com/docs/en/plugins/mods/admin
- `[sdk-ts]` https://code.claude.com/docs/en/agent-sdk/typescript
- `[prompt-caching]` https://code.claude.com/docs/en/prompt-caching
- `[env-vars]` https://code.claude.com/docs/en/env-vars
- `[statusline]` https://code.claude.com/docs/en/statusline
- `[costs]` https://code.claude.com/docs/en/costs
- `[monitoring]` https://code.claude.com/docs/en/monitoring-usage
- `[advisor]` https://code.claude.com/docs/en/advisor
- `[fast-mode]` https://code.claude.com/docs/en/fast-mode
- `[changelog]` https://code.claude.com/docs/en/changelog
- `[plat-models]` https://platform.claude.com/docs/en/about-claude/models/overview
- `[plat-pricing]` https://platform.claude.com/docs/en/about-claude/pricing
- `[plat-deprecations]` https://platform.claude.com/docs/en/about-claude/model-deprecations
- `[plat-choosing]` https://platform.claude.com/docs/en/about-claude/models/choosing-a-model
- `[plat-optimizing]` https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence
- `[plat-models-api]` https://platform.claude.com/docs/en/api/models/list

## Answer table

"Settings hook" means a `hooks/hooks.json` command hook, which is what the plugin uses today. "Mod" means a JavaScript hooks module in the same plugin (`hooks/hooks.json` `modules` key), available from v2.1.287 `[mods-overview]`.

| Unit of work | Can a plugin set model / effort? | Mechanism | Source |
| :- | :- | :- | :- |
| Main conversation, whole session | Not from a settings hook. A plugin can't write `/model` or settings through hook output | User only: `/model`, `--model`, `ANTHROPIC_MODEL`, `model` setting, `ANTHROPIC_DEFAULT_MODEL` | `[model-config]#setting-your-model`, `[hooks]#decision-control` |
| Main conversation, per request | **Yes, with a mod** | `turn.step` hook: `next({ ...e, model })`, `next({ ...e, effort })` | `[mods-ref]#turns`, `[mods-events]#send-a-request-to-a-different-model` |
| Main conversation, per turn | Partly | Skill or command frontmatter `model` / `effort` applies for the rest of the turn the skill runs in (the plugin's own skills only) | `[skills]#frontmatter-reference` |
| Agent tool subagents (built-in, plugin, project, user) | **Yes** | PreToolUse `updatedInput` with `model` (alias only) and `effort` (v2.1.292+). A mod's `agent.spawn` `model` outranks the per-call parameter | `[sub-agents]#choose-a-model`, `[sub-agents]#choose-an-effort-level`, `[sdk-ts]#agent` |
| Teammates (agent teams) | **Yes** | Launched by an `Agent` call with `name`, so the same PreToolUse hook fires. `effort` on that call works for teammates from v2.1.329. Mod `agent.spawn` `model` replaces the first source | `[agent-teams]#specify-teammates-and-models`, `[agent-teams]#how-claude-starts-agent-teams`, `[changelog]` 2.1.329 |
| Workflow agents (`agent()` in a Workflow script) | Partly | Mod `agent.spawn` can change `model` (binary check, below). PreToolUse fires on `Workflow`, so `updatedInput` could rewrite an inline `script` (UNCONFIRMED in practice). `CLAUDE_CODE_SUBAGENT_MODEL` / `CLAUDE_CODE_SUBAGENT_EFFORT` are user env | `[workflows]#cost`, `[hooks]#pretooluse`, `[env-vars]` |
| Forked skills (`context: fork`) | Partly | Skill frontmatter `model` / `effort` set the fork's model (own skills only). Mod `agent.spawn` and `turn.step` (with `e.agentId`) likely apply, UNCONFIRMED for this path | `[skills]#run-skills-in-a-subagent` |
| Inline skills and slash commands | Own skills only | `model` / `effort` frontmatter; plugin manifest `commands` entries have a `model` key | `[skills]#frontmatter-reference`, https://code.claude.com/docs/en/plugins/manifest-reference |
| Conversation forks (`/fork`, fork mode) | No direct control | Forks run on the parent's model, even under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` | `[sub-agents]#run-every-subagent-on-one-model` |
| Background subagents (`run_in_background`) | **Yes** | Same Agent tool call and PreToolUse hook | `[hooks]#agent` |
| Background sessions (agent view dispatch) | Not from a hook | Dispatch picker, settings `env`, `/model` in that session. That session loads its own plugins and hooks | https://code.claude.com/docs/en/agent-view#permission-mode-model-and-effort |
| `claude -p` / Agent SDK sessions | Host only | `--model`, `--effort`, SDK `setModel()`, `applyFlagSettings()`. Settings hooks and mods both run there | `[model-config]`, `[mods-overview]#where-mods-run` |
| Prompt and agent hooks (`type: prompt/agent`) | Own hooks only | `model` field in the hook entry. Default is the background model. Aliases accepted from v2.1.330 | `[hooks]#prompt-and-agent-hook-fields`, `[changelog]` 2.1.330 |
| MCP sampling | No | Not documented anywhere in the Claude Code docs. UNCONFIRMED that Claude Code supports it at all | `mcp.md` has no "sampling" |
| Compaction | No | No setting. Runs on the conversation's model (it reads the conversation's cache). Fallback chain covers it | `[prompt-caching]#compacting-the-conversation`, `[model-config]#which-entries-claude-code-tries` |
| Background functionality (titles, summaries, web-fetch) | No | User env `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `[model-config]#environment-variables`, https://code.claude.com/docs/en/tools-reference#which-model-the-web-fetch-subagent-runs-on |
| Auto-mode classifier | No | User env `CLAUDE_CODE_AUTO_MODE_MODEL` | `[env-vars]` |
| Advisor | No | User `advisorModel`, `/advisor`, `--advisor` | `[advisor]` |
| Status line | Not a model user | Reads `model.id`, `effort.level`, `cost`, `rate_limits` | `[statusline]` |

## Q1. Control per unit of work

### Main conversation

- **No settings-hook field sets the session model.** The decision-control table lists every output field per event. None sets a model. `UserPromptSubmit` "can't replace the prompt; it only injects `additionalContext`" `[hooks]#decision-control`. `PreModelSwitch` can only allow, deny or ask, and doesn't accept `updatedInput` `[hooks]#premodelswitch-decision-control`.
- **A mod can change the model of every request.** `turn.step` fires when "Claude Code is about to send one request to the model. A turn with tool calls has several. `e.agentId` is set for a subagent's request", and a hook can "send it to a different model with `next({ ...e, model })`" `[mods-events]#follow-a-turn`. The reference table also lists `next({ ...e, effort })` `[mods-ref]#turns`. The binary confirms the check: `model` must be a non-empty string, and `effort` must be one of `low, medium, high, xhigh, max` or unchanged (2.1.330 binary, `turn.step` `checkArgument`). Aliases work from v2.1.309 `[mods-events]#send-a-request-to-a-different-model`.
- **`turn.step` isn't blocked by the built-in guard.** The guard (`sec-default@builtin`) loads on Team/Enterprise logins or machines with managed settings. It blocks user mods' hooks on `classic.*`, `prompt.compose`, `prompt.section`, `prompt.context`, `prompt.attachment`, `skill.prompt`, `attribution.text`, `telemetry.log`, `session.compact`, `settings.read` and some `session.append` rows. `turn.step` and `agent.spawn` aren't on that list `[mods-admin]#know-which-hooks-of-a-users-mod-dont-run`.
- **Skill frontmatter** `model`: "applies for the rest of the current turn and isn't saved to settings. The session model resumes when you send your next prompt." `effort` works the same way `[skills]#frontmatter-reference`. From 2.1.330, such a skill leaves "subagents, forks, compaction and `/recap` on the session's model" `[changelog]` 2.1.330.
- **Can a hook inject `/model`?** No documented path for settings hooks. A mod has `prompt.submit` (rewrite text), `$.command.run` and `$.config.set` `[mods-ref]#mods-api-methods`. Whether any of these can run `/model` or set the Model row is **UNCONFIRMED**. `turn.step` makes it unnecessary.
- Hooks can't read the live model except: `SessionStart` input `model` (can be missing), and `from_model` / `to_model` on `PreModelSwitch` and `PostModelSwitch`. "There is no `$CLAUDE_MODEL` environment variable" `[hooks]#common-input-fields`. Effort is readable: `effort.level` in hook input on tool-context events, and `$CLAUDE_EFFORT` `[hooks]#common-input-fields`.

### Agent tool subagents

- Tool input (`AgentInput`): `description`, `prompt`, `subagent_type?`, `model?: "sonnet" | "opus" | "haiku" | "fable"`, `effort?: "low" | "medium" | "high" | "xhigh" | "max"`, `run_in_background?`, `name?`, `isolation?: "worktree" | "remote"`; `team_name` and `mode` are deprecated and ignored `[sdk-ts]#agent`. The hooks page lists only `prompt`, `description`, `subagent_type`, `model` `[hooks]#agent`, so trust the SDK type for `effort`.
- "On an Agent tool call, the parameter takes a model alias, not a full model ID or `inherit`" `[sub-agents]#choose-a-model`. `best`, `opusplan` and `[1m]` forms are not in the enum.
- Model order, first wins: per-invocation `model`, then the definition's `model` (including `inherit`), then `CLAUDE_CODE_SUBAGENT_MODEL`, then the parent's model. "If an installed mod sets a model in its `agent.spawn` hook, Claude Code uses that model in place of the per-invocation parameter" `[sub-agents]#choose-a-model`.
- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` with `CLAUDE_CODE_SUBAGENT_MODEL`: definitions' `model` is ignored and "Claude can't pass a model when it starts a subagent". Forks and `model: inherit` forked skills stay on the parent model `[sub-agents]#run-every-subagent-on-one-model`.
- Same-family rule: with `model: opus` and a parent already on an Opus version, the subagent runs that parent version, not the alias's newest `[sub-agents]#which-version-a-model-alias-selects`.
- Effort order for a subagent: per-call `effort` beats the frontmatter `effort`; `CLAUDE_CODE_EFFORT_LEVEL` beats both (v2.1.292+). With neither set: `CLAUDE_CODE_EFFORT_LEVEL`, then the spawning agent's or turn's level, then `CLAUDE_CODE_SUBAGENT_EFFORT`, then the session level, but only when the subagent runs on the main conversation's model `[sub-agents]#choose-an-effort-level`, `[model-config]#effort-level-of-subagents-and-teammates`.
- Built-ins: Explore and Plan inherit the parent model. `CLAUDE_CODE_SUBAGENT_MODEL` alone doesn't change them. With a Fable parent, Explore runs on Opus on subscription/Console/gateway and stays on Fable on Bedrock, Agent Platform, Foundry, Claude Platform on AWS and apps gateway. `claude-code-guide` and `web-fetch` run on Haiku, `statusline-setup` on Sonnet `[sub-agents]#built-in-subagents`.
- Hook events that fire: `PreToolUse` / `PostToolUse` / `PostToolUseFailure` with matcher `Agent` (the old `Task` name is an alias) `[sdk-ts]#agent`; `SubagentStart` (context only, can't block or set model); `SubagentStop` `[hooks]#subagentstart`. PreToolUse fires for calls made inside subagents too, with `agent_id` / `agent_type` in the input `[hooks]#common-input-fields`.
- PostToolUse `tool_response` for a foreground run: `status`, `agentId`, `content`, `resolvedModel`, `modelsUsed` (only when swapped mid-run), `totalTokens` and `usage` (final request only), `totalDurationMs`, `totalToolUseCount`. Background launches return `status: "async_launched"` with `resolvedModel` and no usage `[hooks]#agent`.
- Plugin agent frontmatter supports `model` and `effort`. `permissionMode`, `hooks`, `mcpServers`, `initialPrompt` are ignored for plugin agents https://code.claude.com/docs/en/plugins/components#frontmatter-fields-in-plugin-agents.

### Mod `agent.spawn`

- Fires when "a subagent or an agent team teammate is about to start, or a subagent that finished or stopped is about to run again"; returns `next({ ...e, model })` or `{ deny }` `[mods-ref]#subagents`.
- Binary check (2.1.330): event fields include `tool_use_id, name, fork, isTeammate, workflow, resume, parentModel, permissionMode, parentAgentId, provider, prompt, description, subagentType, model, background, cwd`. A rewrite may change `model` for ordinary subagents, teammates, workflow agents and forks. For a teammate, `background` and `cwd` "do not apply"; for a workflow agent, `description`, `subagentType`, `background` and `cwd` "do not apply"; for a resumed run every change "was ignored". No `effort` field on this event; use `turn.step` with `e.agentId` for effort (inference from the two event definitions, UNCONFIRMED end to end).
- `model` is checked only as a string, so a full model ID may be accepted here, unlike the Agent tool's alias enum. UNCONFIRMED that Claude Code then honors a full ID.

### Teammates

- Spawned by an `Agent` call that carries `name` while agent teams are enabled; "Claude Code doesn't ask you to confirm the launch" `[agent-teams]#how-claude-starts-agent-teams`. So the plugin's existing PreToolUse hook already sees teammate launches.
- Model order: model named in the spawn, definition `model`, `CLAUDE_CODE_SUBAGENT_MODEL` (unless `inherit`), lead's model. Mod `agent.spawn` replaces the first source `[agent-teams]#specify-teammates-and-models`. "A teammate's model and fast mode are fixed when it spawns."
- Teams need an interactive session; in `-p` and SDK, a named subagent runs as an ordinary subagent `[agent-teams]`.

### Workflow agents

- "A model the script names for a stage counts as the per-invocation model in that order. When nothing else assigns one, the agent runs on your session's model" `[workflows]#cost`. The exact `agent()` option name for model or effort isn't on the docs page; **UNCONFIRMED** (the bundled `/workflow-authoring` skill holds the script reference).
- `CLAUDE_CODE_SUBAGENT_MODEL`, `_FORCE` and `CLAUDE_CODE_SUBAGENT_EFFORT` cover workflow agents `[env-vars]`.
- `Workflow` tool input: `script`, `name`, `scriptPath`, `args`, `resumeFromRunId` `[sdk-ts]#workflow`. PreToolUse matches `Workflow` `[hooks]#pretooluse`. Rewriting a `script` string is possible in principle; `name` and `scriptPath` runs would need the file read and inlined. UNCONFIRMED whether a rewritten script passes auto mode (see Q6).

### Prompt and agent hooks

- Config fields: `prompt`, `model`, `timeout` (30 s prompt, 60 s agent), `continueOnBlock` (prompt only). Default model: "the model Claude Code uses for background functionality" `[hooks]#prompt-hook-configuration`. That is the `haiku` alias or `ANTHROPIC_DEFAULT_HAIKU_MODEL` `[model-config]#environment-variables`.
- A `deniedModels` entry that blocks an agent hook's `model` makes that request run on the session's model `[model-config]#block-specific-models-or-versions`.

### Other internals

- Compaction: a separate request that "repeats your conversation's system prompt, tools, and the history", reading the prefix from cache `[prompt-caching]#compacting-the-conversation`. Each model has its own cache `[prompt-caching]#switching-models`, so compaction runs on the conversation's model (inference). Teammates compact on their own model from 2.1.327 `[changelog]`. A mod can't skip compaction where the guard loads (`session.compact` is blocked) `[mods-admin]`.
- Prompt suggestions use the session's model and its cache `[costs]#background-token-usage`.
- Subagent progress summaries use "the default small fast model" on the Anthropic API from 2.1.328 `[changelog]`.

## Q2. Model selection controls

- **Aliases** `[model-config]#model-aliases`: `default` (clears the override, not an alias), `best` (same as `opus` from v2.1.321), `fable`, `sonnet`, `opus`, `haiku`, `sonnet[1m]`, `opus[1m]`, `opusplan`. On the Anthropic API they resolve to Opus 5.5, Sonnet 5.5, Haiku 5.5; `fable` to Fable 5.2 (Fable 5 in apps-gateway sessions). Other providers lag: Bedrock and Agent Platform resolve `sonnet` to Sonnet 4.5 and `haiku` to Haiku 4.5; Foundry resolves `opus` to Opus 4.6.
- `[1m]` has no effect where the model already has a native 1M window (Fable, Sonnet 5+, Haiku 5.5, Opus 4.7+) `[model-config]#extended-context`.
- `opusplan`: Opus in plan mode, Sonnet otherwise; each plan-mode toggle is a model switch and a fresh cache `[model-config]#opusplan-model-setting`, `[prompt-caching]#switching-models`.
- **Version minimums**: Opus 5.5 needs v2.1.280, Sonnet 5.5 v2.1.284, Haiku 5.5 v2.1.293, Fable 5.2 v2.1.321 `[model-config]`.
- **Session model priority**: `/model`, then `--model`, then `ANTHROPIC_MODEL`, then the `model` setting, then `ANTHROPIC_DEFAULT_MODEL` `[model-config]#setting-your-model`. `/model` writes `model` to user settings.
- **`availableModels`** (managed for enforcement; otherwise user/project/local lists concatenate). It applies to the main model, alias env vars, fast mode, subagent and teammate models, the Agent tool's `model`, `CLAUDE_CODE_SUBAGENT_MODEL`, skill and command `model`, advisor, and background-agent dispatch. A blocked subagent or teammate model falls back instead of failing; a blocked skill model is ignored. A blocked family alias is replaced by the newest permitted version of that family on the Anthropic API and Claude Platform on AWS `[model-config]#restrict-model-selection`. `deniedModels` and `availableModelsMatch: "exact"` are managed-only (v2.1.283+). `enforceAvailableModels` extends the list to the Default option.
- **Enterprise org restrictions** come with the account's entitlements, not settings. Haiku can't be disabled. Not visible in any settings file `[model-config]#organization-model-restrictions`.
- **`ANTHROPIC_DEFAULT_{FABLE,OPUS,SONNET,HAIKU}_MODEL`** set what each alias resolves to; `_NAME`, `_DESCRIPTION`, `_SUPPORTED_CAPABILITIES` (`effort`, `xhigh_effort`, `max_effort`, `thinking`, `adaptive_thinking`, `interleaved_thinking`) for third-party IDs `[model-config]#environment-variables`. `modelOverrides` maps Anthropic IDs to provider IDs.
- **`CLAUDE_CODE_SUBAGENT_MODEL`** is the default for subagents, teammates and workflow agents with no other model; `_FORCE=1` (v2.1.257+) forces it `[env-vars]`. **`CLAUDE_CODE_SUBAGENT_EFFORT`** sets subagent and workflow-agent default effort, not forks, forked skills or teammates `[env-vars]`.
- **Effort levels**: Fable models and Opus 5.5, Sonnet 5.5, Haiku 5.5, Opus 5, Sonnet 5, Opus 4.8, Opus 4.7 accept `low, medium, high, xhigh, max`; Opus 4.6 and Sonnet 4.6 accept `low, medium, high, max`. An unsupported level falls to the highest supported at or below it `[model-config]#adjust-effort-level`.
- **Session effort order**: `CLAUDE_CODE_EFFORT_LEVEL`, then `/effort` / `/model` / `applyFlagSettings()`, then `--effort`, then settings (`modelSettings` per model, `effortLevel`), then the model default. In Claude Code, Fable 5.2, Opus 5.5, Sonnet 5.5 and Haiku 5.5 default to `medium`; Opus 4.7 to `xhigh`; others to `high` `[model-config]#adjust-effort-level`. Note the API defaults differ: `high` for Fable 5.2 and Sonnet 5.5 `[plat-models]`. `maxEffortLevel` (managed) and Enterprise per-role caps limit all of it `[model-config]#organization-effort-limits`. `CLAUDE_CODE_EFFORT_LEVEL=auto` sends no effort.
- `ultracode` is a setting, not an effort level; `--effort ultracode` also sets `xhigh` `[model-config]#adjust-effort-level`. `ultrathink` in a prompt raises that turn to `xhigh` on supported setups.
- **Fast mode**: `/fast`, `fastMode` setting; Fable 5.2, Opus 5.5, Opus 5, Opus 4.8 on the Anthropic API; not Sonnet or Haiku. Subscription users pay through usage credits only. Opus 4.8 fast mode is removed November 30, 2026 `[fast-mode]`.
- **Fallback chains**: `--fallback-model a,b` or `fallbackModel: [...]`, up to 3, for one turn, on overload or non-retryable server errors only. Rate-limit, auth and billing errors never switch. Applies to subagents (v2.1.247+) and compaction (never to a smaller window) `[model-config]#fallback-model-chains`.
- **Plugin `userConfig`**: typed options (`string`, `number`, `boolean`, `directory`, `file`), `options` for pickers (v2.1.271+), shown as `/config` rows (v2.1.269+). Hooks get them as `CLAUDE_PLUGIN_OPTION_<KEY>` env vars; exec-form `args`, skill and agent content get `${user_config.KEY}` https://code.claude.com/docs/en/plugins/manifest-reference. Mods get them as `options` in `register(on, options)` `[mods-ref]#files`.
- **Plugin-shipped settings**: no manifest key or plugin file that sets `model`, `env` or other settings is documented in the plugin reference pages. **UNCONFIRMED**; assume a plugin can't ship `CLAUDE_CODE_SUBAGENT_MODEL` or `availableModels`.
- **Can a hook switch the main model?** Settings hook: no. Writing `model` into a settings file from a hook would be a file write the user didn't ask for, and the docs only describe `model` as read at startup or written by `/model`; mid-session effect **UNCONFIRMED**. Mod: yes per request via `turn.step` (this changes what runs, not the session's saved model).

## Q3. Model discovery

- **Models API** `GET /v1/models` (`[plat-models-api]`): per model `id`, `display_name`, `created_at`, `line` (`haiku|sonnet|opus|fable|mythos|null`), `lifecycle` (`active|deprecated|retired`), `deprecated_at`, `retires_at`, `max_input_tokens`, `max_tokens`, and `capabilities` (`batch`, `citations`, `code_execution`, `context_management`, `effort` with per-level `supported`, `image_input`, `pdf_input`, `server_tools`, `structured_outputs`, `thinking.types.{adaptive, between_tools, disabled, enabled}`). Query filter `lifecycle`. Auth in the example is `Authorization: Bearer $ANTHROPIC_API_KEY`. It needs an API key; whether a claude.ai subscription OAuth token works is **UNCONFIRMED**, and hooks don't receive that token anyway.
- **Gateways**: `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` fills the picker from the gateway's `/v1/models` `[env-vars]`.
- **Without an API key** (subscription login), a plugin can learn:
  - `SessionStart` input `model` (may be absent) `[hooks]#sessionstart-input`
  - `PreModelSwitch` / `PostModelSwitch` `from_model`, `to_model`, `requested_model`, `source` (`command|picker|sdk`, plus `auto|resume` on Post), and cost fields `[hooks]#premodelswitch-input`
  - Agent `PostToolUse` `resolvedModel` and `modelsUsed` `[hooks]#agent`
  - The transcript JSONL: each `type: "assistant"` entry carries `message.model` and `message.usage` (observed locally on 2.1.330: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `service_tier`, `speed`, `inference_geo`, others). Not documented as a stable format; `transcript_path` "may lag" `[hooks]#common-input-fields`.
  - Mods: `$.session.model`, `$.session.usage()` `[mods-ref]#mods-api-methods`; `turn.step` `result.usage.model` `[mods-events]#follow-a-turn`
  - SDK hosts only: `supportedModels()` returns `ModelInfo` with `value`, `resolvedModel`, `displayName`, `supportsEffort`, `supportedEffortLevels`, `supportsAdaptiveThinking`, `supportsFastMode`, `supportsAutoMode` `[sdk-ts]#modelinfo`. A plugin hook can't call this on the running session.
  - Settings files: `availableModels`, `deniedModels` in user, project, local and managed settings (paths per https://code.claude.com/docs/en/settings). Enterprise org restrictions are **not** readable.
- **Bedrock / Agent Platform**: Claude Code runs startup model checks and remembers refused models for up to a day (`CLAUDE_CODE_SKIP_MODEL_ACCESS_MEMORY`) `[env-vars]`. That memory isn't exposed to hooks (UNCONFIRMED where it lives).
- Nothing tells a hook ahead of time which aliases the Agent tool will accept on this provider. The current "rewrite rejected twice" probe in ARCHITECTURE.md remains the only signal for settings hooks.

## Q4. Change feeds

| Feed | URL | Format | Notes |
| :- | :- | :- | :- |
| Claude Code changelog RSS | https://code.claude.com/docs/en/changelog/rss.xml | RSS 2.0, one `<item>` per version, HTML in `content:encoded` | Latest item 2.1.330, `lastBuildDate` 2026-11-21 (fetched) |
| Claude Code CHANGELOG | https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md | Markdown | The docs changelog page "is generated from" it `[changelog]` |
| GitHub releases | https://github.com/anthropics/claude-code/releases.atom | Atom | HTTP 200 when fetched |
| Claude Code docs index | https://code.claude.com/docs/llms.txt (full text: `/docs/llms-full.txt`, about 10 MB) | Markdown link list | Every page also serves `.md` |
| Model aliases and version history | https://code.claude.com/docs/en/model-config.md, sections "Model aliases" and "Version history" | Markdown tables | The version-history table names the Claude Code version at which each alias moved |
| Platform docs index | https://platform.claude.com/docs/llms.txt | Markdown link list | |
| Models overview | https://platform.claude.com/docs/en/about-claude/models/overview.md | Markdown | IDs, prices, windows, retirement floors |
| Deprecations | https://platform.claude.com/docs/en/about-claude/model-deprecations.md | Markdown table "Model status" | At least 60 days' notice before retirement `[plat-deprecations]` |
| Pricing | https://platform.claude.com/docs/en/about-claude/pricing.md | Markdown | |
| Platform release notes | https://platform.claude.com/docs/en/release-notes/overview.md | Markdown | |
| Models API | `GET https://api.anthropic.com/v1/models` | JSON | `lifecycle`, `deprecated_at`, `retires_at`; needs an API key |

All URLs above returned HTTP 200 on 2026-11-22. An RSS feed for platform release notes or deprecations wasn't found; **UNCONFIRMED** that one exists.

## Q5. Current lineup (Anthropic API)

From `[plat-models]` and `[plat-pricing]`. Prices per MTok. Context window and max output are for the synchronous Messages API.

| Model | API ID and alias | Context | Max output | Input | 5m cache write | 1h cache write | Cache read | Output | Thinking | API default effort | Claude Code default effort | Fast mode |
| :- | :- | :- | :- | :- | :- | :- | :- | :- | :- | :- | :- | :- |
| Fable 5.2 | `claude-fable-5-2` | 1M | 128K | $8 | $10 | $16 | $0.20 | $40 | Adaptive, always on | `high` | `medium` | Yes, $16 / $80 |
| Opus 5.5 | `claude-opus-5-5` | 1M | 128K | $4 | $5 | $8 | $0.20 | $20 | Adaptive, always on | `medium` | `medium` | Yes, $8 / $40 |
| Sonnet 5.5 | `claude-sonnet-5-5` | 1M | 128K | $2 | $2.50 | $4 | $0.10 | $10 | Adaptive | `high` | `medium` | No |
| Haiku 5.5, prompt up to 100K | `claude-haiku-5-5` | 1M | 128K | $0.10 | $0.125 | $0.20 | $0.01 | $0.50 | Adaptive | `medium` | `medium` | No |
| Haiku 5.5, prompt over 100K | same | | | $0.50 | $0.625 | $1 | $0.05 | $2.50 | | | | |
| Fable 5.1 | `claude-fable-5-1` | 1M | 128K | $10 | $12.50 | $20 | $0.25 | $50 | | | | |
| Fable 5 | `claude-fable-5` | 1M | 128K | $10 | $12.50 | $20 | $1 | $50 | | | | |
| Opus 5 | `claude-opus-5` | 1M | 128K | $5 | $6.25 | $10 | $0.50 | $25 | | `high` (Claude Code) | | $10 / $50, research preview |
| Opus 4.8 | `claude-opus-4-8` | 1M | 128K | $5 | $6.25 | $10 | $0.50 | $25 | | | | $10 / $50, removed 2026-11-30 |
| Opus 4.7 | `claude-opus-4-7` | 1M | 128K | $5 | $6.25 | $10 | $0.50 | $25 | | | `xhigh` | No |
| Opus 4.6 | `claude-opus-4-6` | 1M | 128K | $5 | $6.25 | $10 | $0.50 | $25 | | | | |
| Opus 4.5 | `claude-opus-4-5-20251101` | 200K | 64K | $5 | $6.25 | $10 | $0.50 | $25 | | | | |
| Sonnet 5 | `claude-sonnet-5` | 1M | 128K | $2 | $2.50 | $4 | $0.20 | $10 | | | | |
| Sonnet 4.6 | `claude-sonnet-4-6` | 1M | 128K | $3 | $3.75 | $6 | $0.30 | $15 | | | | |
| Sonnet 4.5 | `claude-sonnet-4-5-20250929` | 200K | 64K | $3 | $3.75 | $6 | $0.30 | $15 | | | | |
| Haiku 4.5 | `claude-haiku-4-5-20251001` | 200K | 64K | $1 | $1.25 | $2 | $0.10 | $5 | | | | |

Mythos 5.2 / 5.1 / 5 (`claude-mythos-*`) are limited-availability twins of Fable at the same prices `[plat-models]`.

- Effort levels per model are in Q2. Thinking can't be turned off on Opus 5.5, Sonnet 5.5, Haiku 5.5 or Fable models in Claude Code `[model-config]#where-you-cant-turn-thinking-off`.
- Cache reads are 0.05x input on Opus 5.5 and Sonnet 5.5, 0.025x on Fable 5.2 and 5.1, 0.1x elsewhere `[plat-pricing]`.
- Claude 4.7 and later use a tokenizer that makes about 30% more tokens for the same text `[plat-pricing]`.
- Haiku 5.5 over 100K prompt tokens: every token of that request costs 5x, cache reads and output included `[plat-pricing]#long-context-pricing`.

Model status `[plat-deprecations]`:

| Model | State | Deprecated | Retirement |
| :- | :- | :- | :- |
| claude-fable-5-2, claude-mythos-5-2 | Active | | Not sooner than 2027-11-09 |
| claude-fable-5-1, claude-mythos-5-1 | Active | | Not sooner than 2027-09-01 |
| claude-fable-5, claude-mythos-5 | Active | | Not sooner than 2027-06-09 |
| claude-mythos-preview | Deprecated | 2026-06-09 | To be announced |
| claude-opus-5-5 | Active | | Not sooner than 2027-09-22 |
| claude-opus-5 | Active | | Not sooner than 2027-07-24 |
| claude-opus-4-8 | Active | | Not sooner than 2027-05-28 |
| claude-opus-4-7 | Active | | Not sooner than 2027-04-16 |
| claude-opus-4-6 | Active | | Not sooner than 2027-02-05 |
| claude-opus-4-5-20251101 | Active | | Not sooner than 2026-11-24 |
| claude-opus-4-1-20250805 | Retired | 2026-06-05 | 2026-08-05 |
| claude-opus-4-20250514 | Retired | 2026-04-14 | 2026-06-15 |
| claude-sonnet-5-5 | Active | | Not sooner than 2027-09-28 |
| claude-sonnet-5 | Active | | Not sooner than 2027-06-30 |
| claude-sonnet-4-6 | Active | | Not sooner than 2027-02-17 |
| claude-sonnet-4-5-20250929 | **Deprecated** | 2026-09-30 | **2026-11-30** (replacement `claude-sonnet-5-5`) |
| claude-sonnet-4-20250514 | Retired | 2026-04-14 | 2026-06-15 |
| claude-haiku-5-5 | Active | | Not sooner than 2027-10-07 |
| claude-haiku-4-5-20251001 | Active | | Not sooner than 2026-10-15 |
| claude-3-7-sonnet, claude-3-5-haiku, claude-3-haiku | Retired | | 2026-02-19 / 2026-02-19 / 2026-04-20 |

Sonnet 4.5 retiring on 2026-11-30 matters here: it is what `sonnet` resolves to on Bedrock, Agent Platform and Foundry in Claude Code `[model-config]#model-aliases`. Those platforms set their own lifecycle dates, except Foundry, which follows the Claude API `[plat-models]`.

## Q6. Things that change the design

- **Timeouts.** Default 600 s for command hooks; 30 s on `UserPromptSubmit`, `PreModelSwitch`, `PostModelSwitch`; 30 s prompt hooks, 60 s agent hooks `[hooks]#common-fields`. A timed-out PreToolUse command hook doesn't block; the call runs unrouted unless `onFailure: "block"` (v2.1.295+) `[hooks]#timeouts`. A timed-out `PreModelSwitch` hook blocks the switch. Mod hooks have a `next.budget` `[mods-ref]#the-hook-function`.
- **`updatedInput` replaces, it doesn't merge.** "Replaces the entire input object, so include unchanged fields alongside modified ones." Permission rules are evaluated against the returned input `[hooks]#pretooluse-decision-control`.
- **Auto mode can deny a rewritten call.** With `updatedInput` and no `permissionDecision` in auto mode, "Claude Code can deny the changed call" because the server reviewed the original input. Text: `a hook changed this call's input after auto mode reviewed it`. If Claude repeats the changed input exactly, the hook runs again but "any `updatedInput` it returns is dropped". The exempt list for plugin command hooks is Bash, PowerShell, Monitor, Edit, Write and NotebookEdit; **Agent and Workflow are not on it** `[hooks]#pretooluse-updatedinput-in-auto-mode`. ARCHITECTURE.md reports no denial in auto mode on 2.1.330; the docs say it can happen, at least "in interactive terminal sessions and VS Code sessions on a direct connection to the Anthropic API".
- **Multiple PreToolUse hooks**: precedence `deny > defer > ask > allow` `[hooks]`. Docs don't say how two hooks' `updatedInput` values combine; **UNCONFIRMED**. A mod's `agent.spawn` `model` wins over any per-call `model` `[sub-agents]#choose-a-model`, so another plugin's mod silently overrides this plugin.
- **Mods run before plugin settings hooks**: "`PreToolUse` hooks from other settings files and from plugins run after the last mod" `[mods-admin]#know-which-controls-still-apply`.
- **Model unavailable.** Fallback chains switch on overload, unavailability or non-retryable server errors; rate-limit errors never switch `[model-config]#fallback-model-chains`. Content-classifier fallback: Fable and Opus 5.5 re-run biology on Opus 5 and cyber on Opus 4.8; Sonnet 5.5 cyber on Sonnet 5, biology refuses. In a subagent with "Ask each time", the subagent fails with `Agent terminated early due to an API error` `[model-config]#automatic-model-fallback`. A blocked subagent model falls back to `CLAUDE_CODE_SUBAGENT_MODEL` or the parent's model `[sub-agents]#when-an-allowlist-blocks-a-subagent-model`. A Fable subagent on a plan that bills Fable to usage credits may hit a consent prompt that ends the turn after `dialogExpiry` in background or teammate sessions `[model-config]#fable-and-usage-credits`.
- **2.1.330 changes**: a subagent's model fallback now carries over to agents it starts; prompt hooks accept aliases `[changelog]`. 2.1.327: "subagents launched without a model stay on the model they started on when resumed, including after `/model`" `[changelog]`.
- **Cost and usage a plugin can read**:
  - Status line input: `cost.total_cost_usd` (list price unless `modelPricing`), `cost.total_duration_ms`, `cost.total_api_duration_ms`, `context_window.*`, `rate_limits.five_hour/seven_day.used_percentage` and `resets_at` (Pro, Max, Ultra only), `rate_limits.spend_limit.*` (apps gateway), `model.id`, `effort.level` `[statusline]`. A status line is a user setting; a plugin-shipped status line isn't documented (UNCONFIRMED).
  - Hooks: Agent `PostToolUse` usage (final request only); `PreModelSwitch` `context_tokens`, `prompt_cache_warm`, `cache_ttl`, `estimated_cache_write_usd`, `pricing`; resumed `SessionStart` `context_tokens`, `prompt_cache_likely_expired`, `estimated_cache_write_usd` `[hooks]`.
  - Mods: `$.session.usage()` returns `{ startedAt, context, rateLimits, cost }`; `turn.step` `result.usage` per request; `turn.complete` `e.usage` per turn, also for subagents with `e.agentId` `[mods-ref]`, `[mods-events]`.
  - `-p` / SDK: `total_cost_usd` and `modelUsage` per model (`inputTokens`, `outputTokens`, `thinkingTokens`, cache counts, `costUSD`, `contextWindow`, `maxOutputTokens`, `canonicalModel`, `provider`, `costBasis`) covering main loop, subagents, compaction and workflow agents `[sdk-ts]#modelusage`.
  - OpenTelemetry: `claude_code.cost.usage` (USD) and `claude_code.token.usage` with `model`, `query_source` (`main|subagent|auxiliary|plugin_model_complete|plugin_model_classify`), `agent.name`, `skill.name`, `plugin.name`; request events carry `agent_id`, `parent_agent_id`, `effort` `[monitoring]`. Claude Code strips `OTEL_*` exporter variables from hook processes `[hooks]#common-input-fields`, so a hook can't piggyback on the user's exporter.

## Q7. Lowest cost at very high quality

Official guidance, in the docs' own terms:

- **Tune effort before switching models.** "Tuning effort is often a better lever than switching models" `[plat-choosing]`. "Sweep effort on your own workload before you add a second model" `[plat-optimizing]#tune-effort`.
- **Default starting point is Opus 5.5 at `medium`.** "For most agent workloads, start with Claude Opus 5.5 at its default effort (`medium`)" `[plat-optimizing]#compare-models-on-cost-per-task`. Use Fable 5.2 "when your evals on Claude Opus 5.5 at higher effort still fall short" `[plat-models]`.
- **Compare on cost per completed task, not per token** `[plat-optimizing]`. Measured on the SWE-bench Pro subset:
  - Sonnet 5.5 at `high` (API default): 87.6% solved, $0.11 per solved task
  - Opus 5.5 at `low`: 87.4%, $0.12
  - Opus 5.5 at `medium`: 92.8%, $0.22
  - Opus 5.5 at `high`: 95.3%, $0.29 per task
  - Opus 5.5 at `xhigh`: 96.7%, $0.74 per task
  - Fable 5.1 at `high`: 92.3%, $1.19
  - On 463 problems, Fable 5.2 at `high` solved 96.3% for $1.39, at `low` 94.6% for $0.39; Opus 5.5 at `high` 95.5% for $0.30
- **Cheaper model at high effort vs stronger model at low effort.** The docs measured both and found them about equal: "Opus 5.5 at `low` solved about the same share of tasks as Sonnet 5.5 at its default, inside run-to-run noise, for about 7% more per solved task" `[plat-optimizing]`. Neither is recommended over the other in general; the advice is to price both on your own traffic.
- **Lowering Sonnet 5.5 effort hurts coding.** "At `medium`, Claude Sonnet 5.5 scored about 7 points lower than at its default, `high`, on the SWE-bench Pro subset for about 59% of the cost per task", and 62.7% vs 77.8% on an internal agentic-coding benchmark `[plat-optimizing]#tune-effort`. Claude Code runs Sonnet 5.5 at `medium` by default `[model-config]#adjust-effort-level`.
- **Re-run failures at higher effort** when there is a reliable check: `low` with failures re-run at `high` solved about 97% for about $0.17 per task, against 95.3% for $0.29 with everything at `high` `[plat-optimizing]#re-run-failures-at-higher-effort`.
- **Research tasks vary.** Fable 5.2 on DeepResearch Bench II: 54.5% at `low` ($1.49), 57.7% at `high` ($4.82). Opus 5.5 lost 13.3 points at `low` vs `medium` on DRACO, and 34 points on a defect-search benchmark `[plat-optimizing]#tune-effort`.
- **Upgrade first.** Opus 5 to Opus 5.5: about the same score, 78% less per solved task; Haiku 4.5 to Haiku 5.5: 62% to 93% pass, about 84% less per passed attempt `[plat-optimizing]#upgrade-the-model`.
- **Haiku 5.5** "suits high-volume and latency-sensitive work with checkable outputs"; the selection matrix names "subagent tasks" `[plat-choosing]`. As Opus 5.5 subagents on coding, Haiku 5.5 cut cost 32% and the pass rate about 4 points, "about what a mix of `low` and `medium` without subagents would give" `[plat-optimizing]` (delegate-bulk-work section).
- **Lead model with subagents** (the docs' delegate-bulk-work strategy) won only for routine work with a long cost tail and for work larger than one context window. Opus 5.5 with Sonnet 5.5 subagents cost about a third less than one Opus 5.5 session per part, same score. Otherwise, the lead model alone at lower effort came out ahead in every case measured `[plat-optimizing]`.
- **Advisor pattern**: Claude Code ships it as an experimental server tool, `/advisor`, `advisorModel`, `--advisor`; Anthropic API, Claude Platform on AWS and Google Cloud (Anthropic managed) only `[advisor]`. Common pairings include "Sonnet main + Opus advisor" and "Haiku main + Opus advisor". Toggling it keeps the main cache; each advisor call re-reads the full transcript uncached `[advisor]#impact-on-prompt-caching`. Measured gain: +1.7 points for 2.1x cost where the executor consulted; none where it rarely consulted `[plat-optimizing]#advisor-strategy-escalate-hard-decisions`. It's a user setting; a plugin can't turn it on.
- **`opusplan`** is the shipped plan-then-execute mode; each plan-mode toggle starts a fresh cache `[prompt-caching]#switching-models`.
- **Prompt caching** "was usually the largest lever" `[plat-optimizing]`. Without caching, SWE-bench Pro cost 3.3x on Opus 5.5 and 3.1x on Sonnet 5.5.
  - "Each model has its own cache." A `/model` switch re-reads the whole history uncached; so does a skill turn on a different `model` `[prompt-caching]#switching-models`.
  - Changing effort mid-session keeps the cache on Fable 5.1+, Opus 5.5, Sonnet 5.5 and Haiku 5.5 with an API key or subscription, but not on Bedrock, Agent Platform or an apps gateway `[prompt-caching]#changing-effort-level`.
  - **Subagents don't reuse the parent's cache.** "Its first request doesn't read the parent's cache, because the two prefixes differ." Subagents, workflow agents, teammates, forks and compaction sit in the five-minute TTL bucket even on a subscription, unless `subagentPromptCacheTtl` is set. Forks do read the parent's cache. Resumed subagents read their own earlier cache. Workflow fan-outs of same-prefix agents share a prefix `[prompt-caching]#subagents-and-the-cache`, `[workflows]#prompt-caching-in-a-fan-out`.
  - Fast mode keeps a separate cache from standard speed `[prompt-caching]#turning-on-fast-mode`.
- **Fast mode** costs 2x standard on Fable 5.2 ($16 / $80) and Opus 5.5 ($8 / $40), "same model quality, lower latency, higher cost"; subscriptions pay it from usage credits only `[fast-mode]`. It buys speed, not quality.
- **Agent teams** "use approximately 7x more tokens than standard sessions when teammates run in plan mode"; the cost page recommends Sonnet for teammates `[costs]#manage-agent-team-costs`.
- **Per-subagent cost numbers available**: Agent `PostToolUse` `usage` and `totalTokens` (final request only, so not a run total) `[hooks]#agent`; `/tasks` shows each subagent's model and effort `[sub-agents]#check-a-subagents-model`; OTel `query_source: "subagent"` and `agent.name` `[monitoring]`; mod `turn.step` / `turn.complete` usage with `e.agentId` `[mods-events]#follow-a-turn`; SDK `modelUsage` `[sdk-ts]`.

## Design implications for model-router

- **The main conversation is no longer out of reach.** A mod's `turn.step` can set `model` and `effort` on every request, in the terminal, Desktop, VS Code, `-p` and the SDK, and the built-in guard doesn't block it. This contradicts ARCHITECTURE.md ("A plugin can't switch the model of the main conversation"). Needs a live test on 2.1.330 before the overhaul depends on it.
- **Per-request model switching is expensive.** Each model has its own cache, so moving the main conversation between models re-reads the whole context uncached. Route the main loop at prompt boundaries at most, keep one model per prompt, and prefer delegating hard parts to a subagent over switching the main model mid-turn.
- **Effort is the cheap knob.** On Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.1+, with an API key or subscription, changing effort keeps the cache. Route effort per turn more freely than model. On Bedrock, Agent Platform and apps gateways, effort changes also break the cache.
- **Workflow agents and teammates are reachable.** Teammates go through the same `Agent` call, so the current PreToolUse hook already rewrites them, and `effort` works there from 2.1.329. A mod's `agent.spawn` can set `model` for subagents, teammates, workflow agents and forks. This contradicts the "out of reach" list in ARCHITECTURE.md.
- **Another plugin's mod beats this plugin's rewrite.** `agent.spawn` replaces the per-call model, and mods run before plugin settings hooks. If model-router moves to a mod, it gets that priority itself; if it stays a settings hook, the ledger should compare `resolvedModel` with what it wrote and report overrides.
- **Auto mode can deny the Agent rewrite.** The docs exempt only Bash, PowerShell, Monitor, Edit, Write and NotebookEdit from the "changed after review" denial, and the plugin returns `updatedInput` without `permissionDecision`. ARCHITECTURE.md's "no denial" result may hold only on some surfaces. `agent.spawn` changes the model after the classifier reviewed the call input, which avoids this path (inference).
- **Keep model names to the four aliases on the Agent tool.** The enum is `sonnet | opus | haiku | fable`. `best`, `opusplan`, `[1m]` and full IDs are rejected there. `compat/baseline.json` lists `best`, `opus[1m]`, `sonnet[1m]`, `opusplan`, `default` as aliases; those are valid for `/model` but not for the Agent call. To pin a version (for example `claude-opus-5` for biology work), only `agent.spawn` or a definition's `model` can carry a full ID.
- **Set Sonnet's effort explicitly.** Claude Code runs Sonnet 5.5 at `medium`; Anthropic measured about 7 points lower than `high` on coding. `models.json` gives the `standard` level `effort: null`, so a routed Sonnet subagent gets `medium`, or the session level only when the session is also Sonnet. For quality, route `standard` to Sonnet at `high`.
- **Put Opus 5.5 before Fable on the ladder for coding.** Opus 5.5 at `medium` matched Fable 5.1 at about a fifth of the cost; Opus 5.5 at `high` (95.5%) nearly matched Fable 5.2 at `high` (96.3%) at under a quarter of the cost. Reserve `fable` for long-horizon or ambiguous work, or for a retry after Opus at `high` fails. Opus 5.5 ($4 / $20) is also cheaper per token than Opus 5 ($5 / $25).
- **Use escalation instead of first-pass strength where a check exists.** "Low effort, re-run failures at high" was the cheapest policy measured. The plugin's `verifier` crew and Stop gate can supply that signal: route first at a lower level and escalate on a failed test, instead of picking `complex` up front.
- **Watch Haiku's 100K cliff.** Haiku 5.5 bills every token 5x once a request's prompt passes 100K, cache reads included. Fresh subagents start small, so Haiku stays cheap for lookups, but long Haiku runs and Haiku as a main model on a big context lose most of the advantage. `autoCompactWindow` in agent frontmatter takes 100000 or more, so it can't keep a Haiku agent under the line; keep Haiku tasks short.
- **Subagents pay for their own context.** They don't read the parent's cache and get a five-minute TTL. Delegating a tiny task to a strong model costs its fresh system prompt and tool set. Forks read the parent cache but always run on the parent's model.
- **Teams and workflows multiply cost.** Teams use about 7x tokens in plan mode. Workflow fan-outs share a prefix only when model, effort, agent type, tools, schema and cwd match, so assigning different models inside one fan-out costs cache hits.
- **Discovery stays partial.** No hook can see the picker list, Enterprise org restrictions, or Bedrock refusals. Keep the `resolvedModel` and repeated-rewrite probes. Use the Models API (`lifecycle`, `retires_at`, `capabilities.effort`, `max_input_tokens`) only when `ANTHROPIC_API_KEY` is present. A mod can read `$.session.model` and `turn.step` `result.usage.model`.
- **Watch deprecations from the platform page.** Sonnet 4.5 retires 2026-11-30 and is the `sonnet` alias target on Bedrock and Agent Platform in Claude Code. The upstream checker should also fetch `model-deprecations.md` and the model-config "Version history" table, not just the alias table.
- **Use `PreModelSwitch` for cost warnings, not routing.** It fires only on user or client switches, gives `estimated_cache_write_usd`, and a timeout there blocks the switch, so keep that hook fast or leave it out.
- **Read cost from the right place.** For a running total and plan limits, a mod's `$.session.usage()` is the only in-session source a plugin controls. Agent `PostToolUse` usage covers the final request only. OTel variables are stripped from hooks.
- **Keep the 10 s PreToolUse timeout and fail-open design.** A timed-out PreToolUse hook lets the call run unrouted. Don't set `onFailure: "block"`, or a plugin bug would block every agent call.
- **Leave settings alone.** No documented plugin mechanism sets `model`, `env`, `CLAUDE_CODE_SUBAGENT_MODEL` or `advisorModel`. Recommending the advisor, `opusplan` or `subagentPromptCacheTtl` stays advice to the user through `/model-router:*` output, not something the plugin writes.
