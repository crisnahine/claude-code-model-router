# Two paths: the in-process mod and the settings-hook fallback

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.3.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

Checked 2026-11-24 against Claude Code 2.1.331 (`claude --version`), with every page fetched the same day as `.md` from `code.claude.com/docs/en/`. "Type file" means `plugins/model-router/.claude-plugin/types/claude-code/index.d.ts` in the main checkout, headed "Written by Claude Code 2.1.330". The worktree has no copy: the folder is gitignored.

## What we built

- `hooks/hooks.json` lists `./router.ts` under `modules` and ten command hooks. Every command hook runs `sh scripts/model-router.sh <command>`, which starts Python.
- `hooks/router.ts` hooks eight engine events. `agent.spawn` reads the five settings sources, then runs `mod-spawn` through `$.process.run` (8 s timeout) and passes `next({ ...e, model })`. `turn.start` runs `mod-turn` for every main-loop turn. `turn.step` applies the stored effort to each request, for a subagent at any level and for the main loop only as a raise. `turn.complete` awaits `mod-usage` for every turn that has usage, subagent turns included. `tool.call` on `Agent` only reads a caller's `effort`.
- `mod.py`: `spawn` runs `spawn.decide` and `spawn.record`, `turn` decides main-loop effort (`turn_effort`, `_cache_drop`), `usage` writes the ledger. `_heard` sets a per-session `"mod": {"active": true}` entry on every mod call; nothing ever clears it.
- `hooks.pre_agent` returns nothing when `mod.active(session_id)` is true, else `fallback.route`, which rewrites `model` and `effort` through `updatedInput`, keeps a `pending` record, passes a refused rewrite through on its repeat (`_guard`, `REFUSED_AFTER`), and denies once to hold `max_tier`.
- `spawn.py` is shared: `facts`, `decide` (writes nothing) and `record` (ledger, route, votes) serve both paths.
- Per routed subagent on the mod path: one Python start each for `PreToolUse` (stands down), `mod-spawn`, `PostToolUse`, `SubagentStop`, and one `mod-usage` per subagent turn. Per prompt: `UserPromptSubmit` and `mod-turn` both profile the same text.

## What the sources say

Is the mod documented, and does its model win?

- A mod's model takes the first place in the subagent order: "If an installed mod sets a model in its `agent.spawn` hook, Claude Code uses that model in place of the per-invocation parameter." https://code.claude.com/docs/en/sub-agents (Choose a model)
- Same for teammates: "Claude Code uses that model in place of the first source." https://code.claude.com/docs/en/agent-teams (Specify teammates and models)
- `agent.spawn` fires when "A subagent or an agent team teammate is about to start, or a subagent that finished or stopped is about to run again", and returns "`next({ ...e, model })` to choose the model of one that is starting, or `{ deny: reason }`". https://code.claude.com/docs/en/plugins/mods/reference (Subagents)
- Workflow agents are only in the type file and the changelog: "A rewrite of its `prompt` and `model` applies" (type file, `AgentSpawnInput.workflow`); 2.1.300 "Added support for rewriting a workflow agent's `prompt` and `model` in the `agent.spawn` mod hook, which could only refuse workflow agents before". https://code.claude.com/docs/en/changelog
- `agent.spawn` has no effort field (type file, `AgentSpawnInput` lists none). Effort per request is `turn.step`: "`next({ ...e, model })`, `next({ ...e, effort })`" https://code.claude.com/docs/en/plugins/mods/reference (Turns), and "`e.agentId` is set for a subagent's request" https://code.claude.com/docs/en/plugins/mods/events (Follow a turn). The type file adds "A rewrite lasts one step" and that `effort` is "the level its loop, `CLAUDE_CODE_EFFORT_LEVEL`, turn or session set" (`TurnStepInput.effort`).

Is it stable enough?

- No mods page carries an experimental or beta label. "Mods are on by default. In the terminal, use Claude Code v2.1.287 or later." https://code.claude.com/docs/en/plugins/mods/overview (Turn mods on or off)
- The type file opens with "EARLY ACCESS: this surface may change between releases without notice." The create page: "The events and methods can change between releases, so trust these files over any page, this one included, when they disagree." https://code.claude.com/docs/en/plugins/mods/create (Get type definitions for your version)
- The type files are written only "When Claude Code loads a mod from `--plugin-dir` in an interactive session, or a mod Claude wrote for you" (same page), so an installed copy gets none.
- The surface this plugin uses moved often: teammates on `agent.spawn` 2.1.289, workflow agents 2.1.292 (refuse only) and 2.1.300 (model), resumed runs 2.1.299, `turn.step` alias fix 2.1.309, `.catch` meaning changed 2.1.301 ("Changed a mod hook's `.catch` handler to run only when the hook throws or times out"), `hookEnv` 2.1.326. https://code.claude.com/docs/en/changelog
- Anthropic can switch installed mods off: "`hooks modules are turned off for installed plugins in this process: the rollout switch served off`: Anthropic has turned installed mods off remotely." https://code.claude.com/docs/en/plugins/mods/troubleshoot (Refusal messages)

Where each path runs

- Mods run in the terminal, the Desktop Code tab, VS Code, `claude -p`, the Agent SDK, Remote Control, and cloud sessions "for a plugin that reaches the cloud session"; not in Desktop WSL or SSH sessions. "A mod's hooks run whichever provider serves the model, including Amazon Bedrock, Google Cloud's Agent Platform, and Microsoft Foundry." https://code.claude.com/docs/en/plugins/mods/overview (Where mods run)
- `allowManagedModsOnly` stops the mod and leaves the fallback: "Users' other customizations keep working: their hooks in settings files and in plugins' `hooks/hooks.json`". https://code.claude.com/docs/en/plugins/mods/admin (Stop user-installed mods from loading)
- `allowManagedHooksOnly` runs "hooks from plugins your managed settings force-enable", and "A mod in such a plugin loads only when it counts as your organization's". So a force-enabled copy of this plugin can run its settings hooks without its mod. https://code.claude.com/docs/en/settings-reference (What runs under `allowManagedHooksOnly`)
- `disableAllHooks` stops both: "it stops the mods in every installed plugin, yours included, and turns off every hook in settings files". `--bare`: "installed plugins that are not managed load no hooks module in this mode (--bare)", and it skips "auto-discovery of your hooks ... installed plugins". https://code.claude.com/docs/en/plugins/mods/admin (Choose how much to allow), https://code.claude.com/docs/en/plugins/mods/troubleshoot, https://code.claude.com/docs/en/cli-reference
- A mod can be unloaded mid-session while settings hooks keep running: "mods that run in the hooks worker are off for this session: it crashed 3 times ... it unloaded every mod that isn't built in". https://code.claude.com/docs/en/plugins/mods/troubleshoot (A hook is skipped or a mod is unloaded)

Managed logins

- The built-in guard loads on "`/login` credentials for a Team or Enterprise plan" or a machine with managed settings. Hooks of a user's mod that don't run there include `skill.prompt`, `classic.<Event>`, `prompt.compose`, `prompt.section`, `session.compact`, `settings.read`. `agent.spawn`, `turn.start`, `turn.step`, `turn.complete`, `tool.call` and `prompt.submit` are not on the list. https://code.claude.com/docs/en/plugins/mods/admin (Know what happens by default, Know which hooks of a user's mod don't run)
- `router.ts` hooks `skill.prompt` to read a typed command's body, so that part never runs under the guard.

Order of the two paths

- "`PreToolUse` hooks from every other settings file and from plugins' `hooks/hooks.json`: run after the last mod calls `next`, as part of Claude Code's own behavior." https://code.claude.com/docs/en/plugins/mods/events (Where settings hooks run in the order)
- So a mod's `tool.call` hook on `Agent` always runs before this plugin's own `PreToolUse` hook for the same call, and `tool.call` fires for "calls a subagent makes" too (same page, Guard or change a tool call).

Auto mode

- For a settings hook: "When your hook returns `updatedInput` with no `permissionDecision` in auto mode, Claude Code can deny the changed call." A plugin command hook may change "Bash, PowerShell, Monitor, Edit, Write, and NotebookEdit calls without this denial"; Agent is not on that list. https://code.claude.com/docs/en/hooks (PreToolUse `updatedInput` in auto mode)
- For a mod: "If a hook or mod changes an action's input after the server's review, Claude Code can deny the action". The troubleshoot page names "a mod's `tool.call` or `turn.step` hook, or a `PreToolUse` settings hook" as the hooks that can cause it. `agent.spawn` is not named. https://code.claude.com/docs/en/permission-modes (Server-side classifier review), https://code.claude.com/docs/en/plugins/mods/troubleshoot (A tool call is denied)

The supported versions

- `engines`: "plugins outside their stated Claude Code version range are not installed, updated or loaded" (changelog 2.1.323); the field "Requires Claude Code v2.1.323 or later". https://code.claude.com/docs/en/changelog, https://code.claude.com/docs/en/plugins/manifest-reference
- With `"claude-code": ">=2.1.329"`, every version that loads the plugin has mods (2.1.287), teammates and workflow agents on `agent.spawn` (2.1.289, 2.1.300), `hookEnv` (2.1.326) and teammate `effort` (2.1.329).

Python per spawn and per turn

- A mod is "Functions in a plugin that Claude Code calls in its own process"; a settings hook is "A shell command, HTTP request, or prompt". https://code.claude.com/docs/en/plugins/mods/overview (Compare mods, settings hooks, skills, and MCP servers)
- "The hooks module itself has no Node.js APIs ... and no network or file access of its own"; it has `$.fs`, `$.store`, `$.process`, `$.wasm`. https://code.claude.com/docs/en/plugins/mods/api (Reach files, processes, and the network)
- Each such call "is itself an event ... A mod earlier in the chain can observe, rewrite, or refuse your call, which is how an organization restricts what mods reach." Same page.
- A hook's own limit is "10 seconds", "not counting time inside `next` or a mods API call"; "`$.process.run` timeout: 30 seconds by default". https://code.claude.com/docs/en/plugins/mods/reference (Limits)
- `hookEnv` returns "the variables that name the session and this plugin, as its command hook gets them", including `CLAUDE_CODE_SESSION_ID` and `CLAUDE_PLUGIN_DATA` (type file), so a TypeScript mod can read and write the same state folder the Python hooks use.
- Unawaited work from a hook is a documented pattern: "Not awaited: the hook would stay open until the new turn starts". https://code.claude.com/docs/en/plugins/mods/api (Retry a refused or dropped call)
- The repo's own figures: `mod-spawn` and `mod-turn` cost about 56 to 58 ms warm and 186 to 187 ms cold (p50), and the decision itself 0.26 ms (`docs/PERFORMANCE.md`).

## Verdict

Partly right.

The mod is the right primary mechanism. It is the only documented plugin path whose model outranks the caller's, the only one that reaches workflow agents and main-loop effort, and it changes no tool input, so the documented auto-mode denial for an `Agent` rewrite does not apply to `agent.spawn`. Its stability is weaker than the pages suggest: the type file says "EARLY ACCESS", the fields it uses moved in six releases since 2.1.289, and Anthropic can switch installed mods off remotely. That is a reason to keep the fallback, and the sources give the real cases for it: `allowManagedModsOnly`, the rollout switch, a mod unloaded after worker crashes, and a force-enabled plugin under `allowManagedHooksOnly`. "Versions without mods" is not one of them, because `engines` keeps the plugin off those versions. What is wrong is the switch between the two paths. A sticky per-session flag cannot see a mod that was unloaded or refused after it once answered. The Python round trip on every spawn, turn and turn end is allowed by the sources but buys nothing a mod needs a process for.

## What to change

1. Replace the sticky `mod.active` flag with a check on each call. In `router.ts`, the `tool.call` hook on `Agent` writes the call's `tool_use_id` to a small file under `CLAUDE_PLUGIN_DATA` (from `$.process.hookEnv()`, written with `$.fs.write`). It does this only while the mod's last runtime round trip worked, so a mod whose `$.process.run` is refused does not silence the fallback. `hooks.pre_agent` stands down only when its own `tool_use_id` is in that file. This deletes `mod.active`, the flag write in `mod._heard`, and the `"mod"` state entry. It fixes three gaps: a mod unloaded mid-session, a resumed session under a new policy, and the first call of a session racing `mod-turn`. Backed by the documented order ("run after the last mod calls `next`") and the unload case ("it crashed 3 times"), https://code.claude.com/docs/en/plugins/mods/events and https://code.claude.com/docs/en/plugins/mods/troubleshoot. The order and the unload are source facts; this fix is my judgment.
2. Keep the fallback and correct its stated reason. In `docs/ARCHITECTURE.md` (the paragraph that says it "covers Claude Code versions without mods") and in any README text that says the same, list the real cases: `allowManagedModsOnly`, the remote rollout switch, a mod unloaded mid-session, and a managed force-enabled copy under `allowManagedHooksOnly`. Also say it does not reach workflow agents, sets no main-loop effort, and can be denied in auto mode. Required by sources: `engines` (https://code.claude.com/docs/en/changelog, 2.1.323) contradicts the current text, and the cases come from https://code.claude.com/docs/en/plugins/mods/admin and https://code.claude.com/docs/en/settings-reference.
3. Stop awaiting `mod-usage` in the `turn.complete` hook of `router.ts`. Start the call and return `done`. Nothing reads its answer. This takes one blocking Python start off every turn of every agent. Backed by the documented unawaited pattern, https://code.claude.com/docs/en/plugins/mods/api. My judgment.
4. Skip `mod-turn` in `turn.start` when it can't change anything: `options.main_effort` is false and the turn text is not a typed command. `mod.turn` then only writes a ledger row that says why no effort was set. This removes one blocking Python start per prompt in the default setup. The `UserPromptSubmit` hook already profiles the same prompt. Backed by the in-process model of mods, https://code.claude.com/docs/en/plugins/mods/overview. My judgment.
5. Never lower a workflow agent's effort at `turn.step`. `agent.spawn` carries no effort (type file), so the mod can't see an effort the script set, and today `turn.step` applies the pick to an agent even when it is below the request's current level. For a workflow agent, apply the pick only as a raise, as the main loop already does. Touches the `turn.step` hook in `router.ts` (the `apply` test) and needs the agent's kind kept beside `agentEffort`. Backed by the type file (`AgentSpawnInput`, `TurnStepInput.effort`) and https://code.claude.com/docs/en/workflows (Cost). My judgment, and quality ranks above cost here.
6. Say where `skill.prompt` doesn't run. Under the guard (Team or Enterprise login, or managed settings) the `skill.prompt` hook in `router.ts` never fires, so `mod-turn` gets a typed command with no body. Either list it under "Where it goes past the docs" in `docs/CLAUDE_CODE_DOCS.md`, or drop the hook and read the command's text from `prompt.submit` alone. Required by source to document: https://code.claude.com/docs/en/plugins/mods/admin (Know which hooks of a user's mod don't run). Which fix to take is my judgment.
7. Type-check against the newest published types as well as the pinned commit. `tests/mod/typecheck.sh` pins `TYPES_COMMIT`, so a changed or removed field on `AgentSpawnInput` or `TurnStepInput` would pass CI. Add a second, non-pinned run to the daily upstream check. Backed by the type file's "EARLY ACCESS" line and "trust these files over any page", https://code.claude.com/docs/en/plugins/mods/create. My judgment.

Not on the list, by judgment: porting the whole decision to TypeScript. The sources allow it (`$.fs`, `hookEnv`, `CLAUDE_PLUGIN_DATA`), and it would drop the Python dependency and the refusable `$.process.run` from the main path. But the settings hooks still need the Python runtime for the fallback and the bookkeeping, so a port means two deciders that must agree. Items 3 and 4 remove most of the per-turn starts. Revisit the port if the fallback and the Stop gates are cut down in other decisions, so that Python is left with too little to justify two runtimes.

## What the sources do not say

- Whether a model set in `agent.spawn` counts as changed input for auto mode. The docs name `tool.call`, `turn.step` and `PreToolUse` only; `agent.spawn` is not documented either way.
- Whether a `turn.step` effort rewrite counts as changed input for auto mode. Not documented.
- How a subagent's first request is tied to its spawn when that request goes out before `agent.spawn`'s `next` resolves. The type file says `next` "settles with the first attempt's `agentId`"; the ordering against the first `turn.step` is not documented. `router.ts` works around it by matching the prompt text (`inFlight`).
- The cache effect of changing a running subagent's effort with `turn.step`. Not documented.
- Whether two `PreToolUse` hooks' `updatedInput` values combine, or which wins. Not documented beyond the 2.1.326 note that "the rewrite now runs in more cases".
- Whether `session_id` stays the same across `--resume`, which decides how stale the current flag can get. Not documented on the hooks page.
- Whether Python is present on a user's machine, in a cloud session, or in the Desktop app. Claude Code documents no Python runtime; the launcher exits 0 without it, and then both paths do nothing.
- Whether an unawaited `$.process.run` keeps running after its hook returns in every surface, `claude -p` teardown included. The docs show the pattern with `$.prompt.submit` only.
- Whether the remote rollout switch can turn mods off mid-session or only at process start. The messages say "in this process".
- A stability promise or deprecation policy for the mods API. The pages have no label; only the type file says "EARLY ACCESS".
- Whether a `PreToolUse` hook on `Workflow` could route workflow agents by rewriting the script. Not documented, and the fallback doesn't try.
