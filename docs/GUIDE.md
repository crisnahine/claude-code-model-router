<!-- Page head, written by tools/docs_assets.py: edit its PAGES table, then run it. -->
<p align="center"><img src="assets/banner-guide.svg" width="880" alt="Guide: How do I configure it for my setup?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="FAQ.md">Questions</a> &nbsp;&middot;&nbsp; Next: <a href="README.md">Documentation</a></p>
<!-- End of page head. -->

# Guide

How to use, configure and run Model Router, and how it behaves on your setup. The [README](../README.md) is the short version; [ARCHITECTURE.md](ARCHITECTURE.md) has the internals.

## Commands

| Command | What it does |
| :- | :- |
| `/model-router:ship <task>` | Plans, builds, verifies, reviews and commits a change on a branch, and stops before pushing, merging or deploying |
| `/model-router:route <task>` | Explains which model a task would get and why, and how to split it |
| `/model-router:models [reset] [--json]` | Lists the models in use, what each runs as, what is capped or unavailable, and what was learned. `reset` clears it |
| `/model-router:usage [days] [--json]` | Shows what ran where, and what it cost at list price: agents, levels, models, tokens and dollars |
| `/model-router:refresh` | Reviews new Claude Code releases, docs changes, model changes and retirements |

With `--json`, `models` and `usage` print one JSON object instead of the tables. Its `schema` key names the shape and its version, `model-router.models/1` and `model-router.usage/1`. A new key keeps the version; a key removed, renamed or changed in meaning bumps it. Scripts can run the same commands without Claude: `sh <plugin>/scripts/model-router.sh --data-dir <plugin data dir> usage 30 --json`. The shapes are in [ARCHITECTURE.md](ARCHITECTURE.md#machine-readable-output).

## Role agents

The plugin ships seven agents for work that no better-fitting agent covers: `model-router:scout`, `verifier`, `docs-researcher`, `implementer`, `architect`, `reviewer` and `deep-solver`. The scout, verifier and docs-researcher run on Haiku at medium effort, from their own definitions, so they stay cheap even when the router isn't routing: with `routing_mode` at `advise` or `off`, without Python, or with hooks turned off. The other four name no model or effort, so the router routes each by its brief like any other agent; the architect and reviewer start no lower than complex because of their names, and the deep-solver's name floors it at hardest, so a frontier brief can still take it to the strongest model. When the router isn't routing, those four run on `CLAUDE_CODE_SUBAGENT_MODEL` if it's set, else on the conversation's own model, as any agent without a model does (https://code.claude.com/docs/en/sub-agents.md#choose-a-model). Their descriptions say when each is the right tool, such as the reviewer for a risky or cross-module diff, since Claude decides when to delegate from those descriptions (https://code.claude.com/docs/en/sub-agents.md). The architect, reviewer, scout, verifier and docs-researcher have no tool that writes files, and their descriptions say so, so Claude doesn't give one of them a task whose result is a file.

`/model-router:ship <task or issue>` runs a full loop with them: understand, plan, build, verify, review, fix, and commit on a branch. A task of a few edits, in one file or in sequential steps, skips the plan and the build agent and is done directly. It stops before pushing, merging or deploying.

## Configuration

Options, in `/config` under the plugin:

| Option | Default | Meaning |
| :- | :- | :- |
| `routing_mode` | `auto` | `auto` sets model and effort on every agent. `advise` only tells Claude the estimate. `off` does nothing but keep the ledger |
| `priority` | `balanced` | `balanced`: the cheapest model strong enough. `quality`: one step stronger. `fast`: one step lighter, and the quickest that qualifies. Fast sets no effort above `low`, so a lookup runs at Claude Code's own default for Haiku, `medium` (https://code.claude.com/docs/en/model-config.md#adjust-effort-level) |
| `max_tier` | none | The strongest model subagents may run on: `haiku`, `sonnet`, `opus` or `fable`. Anything stronger is lowered to it |
| `agent_pins` | `respect` | `respect` keeps the model an agent names for itself. `override` routes those agents by task too |
| `main_effort` | off | When on, raises the main conversation's effort for a complex or harder request, for that turn. Off where an effort change would re-read the context uncached: Bedrock (including its Mantle endpoint), Vertex, a Claude apps gateway, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`, prompt caching turned off (`DISABLE_PROMPT_CACHING` or a per-model variant), and models other than Fable 5.1+, Opus 5.5, Sonnet 5.5 and Haiku 5.5 (the list is `main.effort_keeps_cache` in `config/models.json`). Also off on Foundry, behind `ANTHROPIC_BASE_URL` and on another third-party provider, where the router can't confirm the cache survives ([third-party providers](#third-party-providers)). Leave it off in a HIPAA organization, which the router doesn't detect, or if Claude Desktop reaches Claude through a Claude apps gateway, which it can't tell from a subscription there |
| `update_check` | on | The daily check of the changelog, docs index, model list and deprecations |
| `verify_gate` | on | Asks for a test run before a turn ends when code changed and nothing checked it. It is the failure signal the router needs to start cheap and rerun what fails ([cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence)), at the price of a turn and sometimes an agent. `false` turns it off |
| `strong_agent_gate` | on | Asks for a stronger agent's check before a turn ends when a weak session model answered a hard request alone. It rarely fires on a strong session model |

Rules and extra models go in a JSON file. `~/.claude/model-router.json` is yours and applies everywhere. `.claude/model-router.json` belongs to a project. Both have the same shape:

```json
{
  "routing_mode": "advise",
  "agents": {
    "general-purpose": { "level": "standard" },
    "my-agent": { "model": "opus" },
    "legacy-agent": { "route": false }
  },
  "levels": { "complex": { "effort": "xhigh" } },
  "models": { "newmodel": { "strength": 3.5, "speed": 2, "cost": 3.5 } }
}
```

Your own file is trusted in full. A project's file only raises. It can turn routing down to `advise` or `off`, add models, and raise any level's model and effort. Its rule for an agent is a floor and a suggestion: the rule's `level` can lift a call above the level its brief earns, and its `model` applies only when it isn't weaker than the router's pick for that call. So a repository you clone can make its subagents run on a stronger model or at more effort, and cost more. It can't lower a level below the shipped value, or below yours when your file sets one, and its `route: false` is ignored: a lowering is dropped without an error. A level's `needs` that isn't a finite number, or any other key of the wrong type, is dropped from every file. `/model-router:models` lists what was dropped and what a project's file asked for that wasn't applied. It can't turn routing on for someone who switched it off, re-rank a model that ships with the plugin or that you defined, or get any model past your cap. A level's `needs` never falls below the level under it, so raising one level lifts the ones above. To bound what a cloned repository can spend, set `max_tier`: it caps the model, and the effort the project set stays.

A file may carry `"schema": 1`, the version of this shape; without it, it is read as version 1. A file written for a newer release still loads: the settings this release understands apply, and `/model-router:models` names the file. Older shapes keep working after an update ([ARCHITECTURE.md](ARCHITECTURE.md#compatibility)).

## Who decides the model

![Who decides the model, strongest first: CLAUDE_CODE_SUBAGENT_MODEL_FORCE, a model named on the call, a per-agent rule, the agent's own model field, CLAUDE_CODE_SUBAGENT_MODEL, then the router's pick. The winner then has to be usable under the cap and your settings, and effort follows the level unless something already sets it.](assets/precedence.svg)

In order, strongest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` in your environment. Every subagent's model is pinned, so the router sets only effort. The session-start note says so once, because a pinned strong model costs far more on lookups: Opus 5.5's list price is 40 times Haiku 5.5's per token, for prompts up to 100,000 tokens ([pricing](https://platform.claude.com/docs/en/about-claude/pricing.md)).
2. A model named on the call itself, by Claude, a skill's instructions or a workflow script. If you ask for a model in your prompt ("use opus for this"), Claude is reminded to pass it on the calls for that request.
3. A per-agent rule in your `~/.claude/model-router.json`, or the project's `.claude/model-router.json` when its model isn't weaker than the router's pick.
4. The agent's own `model` field, unless you set `agent_pins` to `override`. Its effort still follows the subtask when the agent sets none.
5. `CLAUDE_CODE_SUBAGENT_MODEL`, your default for agents without a model.
6. The router's pick.

Under steps 1 and 5 your environment has decided, so the model is left as it is: with `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` the router sets only effort, and with `CLAUDE_CODE_SUBAGENT_MODEL` it leaves the call alone, unless `agent_pins` is `override`. Under steps 2, 3, 4 and 6, whatever wins then has to be usable: inside your cap, allowed by your settings and known to work. If it isn't, the strongest usable model below it runs, or the weakest usable one when none is below it. Effort is left alone when the call, the agent, `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` already sets it. `CLAUDE_CODE_SUBAGENT_EFFORT` doesn't apply to agent-team teammates (Claude Code ignores it for them), so for a teammate the router still sets the effort. Every effort the router sets, for an agent or a main-loop turn, is held to `maxEffortLevel` when a settings source sets one, and the ledger reason and `/model-router:route` say when it was: the lowest cap of any source applies, a source's `modelSettings` entry for a model replaces its own top-level cap for that model, and `max` sets none (https://code.claude.com/docs/en/settings-reference.md#maxeffortlevel). Claude Code applies the cap itself before each request, so the router only reports what will run.

`CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` runs every workflow agent on one model while other subagents keep theirs (https://code.claude.com/docs/en/changelog, 2.1.296). The env-vars page doesn't list it, and nothing says whether it wins over the model a mod sets on `agent.spawn`, so the router doesn't change how it routes workflow agents when it is set: the session-start note and `/model-router:models` name it as a pin on workflow agents, and `/model-router:usage` shows the model each one ran on.

## The checks before a turn ends

![The checks before a turn ends. strong_agent_gate, on by default, blocks the stop once when a complex or harder request was answered by a weaker session model with no agent of the needed strength started, and asks for a stronger agent's check. verify_gate, on by default, asks once for a test run when code changed and nothing checked it.](assets/stop-checks.svg)

Before Claude ends a turn, the `Stop` hook runs two checks. Each asks once, then lets the turn end.

With `strong_agent_gate` on, its default, a complex or harder request that a weaker session model answered alone, with no agent of the needed strength started, gets one blocked stop and a request for a stronger agent's check: the reviewer, given a saved diff, once code changed during the request, else the architect, or the deep-solver for a frontier request. That agent starts no lower than the request's level, so a short hand-off brief can't route it lower, and a redo of it still climbs. A complex request needs Opus, so a Sonnet session is below its need and gets the session note and the stop check.

With `verify_gate` on (the default; `false` turns it off), Claude is asked once for a test run when code in the project changed and no test, lint, type-check or build ran after the last edit, and no verifier or reviewer was started. A test run inside a subagent counts.

## What it can't do

These limits come from what Claude Code lets a plugin do, or from choices made to protect quality and cost.

- It doesn't switch the main conversation's model. A mod could, but each model keeps its own prompt cache, so a switch re-reads the whole conversation uncached. It changes the main conversation's effort instead, which keeps the cache on current models, and when a request looks harder than the session's model, Claude is told that a strong subagent can take the plan or decision when a few reads of the code can't settle it, and that it works directly otherwise. A stop with no strong agent started is held once, unless `strong_agent_gate` is off.
- It leaves forks alone. A fork reads its parent's cache and always runs on its parent's model.
- It leaves alone an agent whose definition it can't read, such as an agent defined on the command line, unless `agent_pins` is `override`. That agent may have a model its author chose. Workflow stages and teammate roles, which the engine makes, are routed.
- It sees a model swap only in a foreground result. A background agent's launch carries no usage fields, so a background run that swaps models is not seen.
- It doesn't judge answers. The profile reads the wording of the brief and the agent's name, never the code, so it is an estimate. Claude can overrule it on any call, and redo escalation and the feedback loop correct levels that prove too low.
- The profile reads English wording. A brief written mostly in another script, such as Japanese or Cyrillic, or with enough common French, Spanish, Portuguese, Italian, German or Dutch words, starts at standard (Sonnet at medium effort) instead of lookup. A very short brief, or one in another language, may still score lookup, and a redo moves it up.
- It doesn't route to a new model family on its own. A new alias in the docs is reported within a day, and it is routed to once it has an entry under `models`. New versions inside a family need nothing: the router uses aliases, and Claude Code moves each alias to the newest version. When your session's model or an alias still runs an older version than the newest of its family, session start says so: usually a `model` setting naming a full version, or Claude Code waiting for an update.
- It does nothing when hooks are switched off, by `disableAllHooks` or by `allowManagedHooksOnly` in a managed setup. Where an organization blocks user mods, routing falls back to the `Agent` call.
- Spend figures are list prices from the registry's `prices` table. Each model request is priced on its own and a turn's figure is the sum, so Haiku 5.5's higher rate for a prompt over 100K tokens applies to exactly the requests that cross it. Fast mode requests are counted at standard rates, because the usage Claude Code reports doesn't say which requests ran fast. A model with no price is listed as such. Add a missing price under `prices` in your own `~/.claude/model-router.json`; a project's file can't set prices. Your bill is in `/usage` and `/cost`.

## Compatibility

### Other plugins

The router never changes which agent runs and never refuses a call to steer it, so it has nothing to conflict over. Another plugin's hook that denies or asks about a call still applies. Another plugin's mod that sets a spawn's model runs in its own order with this one, and the model that ran is what the ledger records.

### Permission modes

The plugin never approves a tool call: your permission rules and auto mode review every spawn as they would without it. The mod sets the model after the call was reviewed, so auto mode's review of a changed call doesn't come into it. The `Agent` call fallback was tested in default and auto mode. Claude Code's documentation says auto mode can refuse a rewritten call once so that it can review the change. Claude then repeats the call as rewritten and it runs.

### Refused rewrites

On the `Agent` call fallback, if a rewritten call comes back without having run, the same call is passed through untouched the next time, so a refused rewrite can't loop. The plugin issues one refusal of its own, to protect your cap. When a call asks for a model above the cap and lowering it didn't take, the repeated call is refused with the model to use instead.

### Organization allowlists

`availableModels` and `deniedModels` are read the way Claude Code reads them. With the mod, the router asks Claude Code for each settings source (`policy`, `user`, `project`, `local` and `flag`) and takes only `availableModels`, `deniedModels`, `availableModelsMatch` and `modelOverrides` from each, counting `modelOverrides` only as set or not. `policy` is managed policy as Claude Code reports it to mods: every managed tier merged, which the mod types say. A read that fails or takes more than two seconds leaves that source out. For each source the router then uses what the mod last sent for it in this session, and only without that its file, so a failed `policy` read never leaves your own lists standing as if no managed policy existed. Without the mod the settings files are read: the managed file and `managed-settings.d/` at their fixed paths, the user and project files, and the local file at the repository root (a worktree's is at the main checkout). A settings file that isn't a regular file under 1 MiB is ignored. The files can't show server-managed, MDM, registry or `--settings` policy. The managed files apply even when the mod reports policy, as a floor under it: their `deniedModels` add to the reported ones, their `availableModels` must permit a model too, and `availableModelsMatch: exact` in either makes matching exact. A `policy` list key (`availableModels` or `deniedModels`) that isn't a list, in the engine's answer or a managed file, leaves no model open too; in the other sources it counts as unset. A managed file that has content but can't be read or parsed as a JSON object leaves no model open; an empty or blank one, or a folder in `managed-settings.d/`, counts as no policy. A `policy` `availableModels` list applies alone; otherwise the lists in the other sources are combined. `deniedModels` counts only in `policy`. Entries are trimmed. An allow list is cut to 200 entries of 200 characters, which only allows less. A `policy` deny list over that size is never cut, since that would let a denied model through: it leaves no model open instead. An allow entry stands for its family, so `claude-opus-4-6` permits `opus`. Within the list that applies, an entry naming a specific model in a family (a version prefix such as `claude-sonnet-4-5` or a full ID: an entry with a digit, or a `claude-` or provider prefix) turns off the entry that names the family, as Claude Code does (https://code.claude.com/docs/en/model-config.md, Merge behavior): with `["sonnet", "claude-sonnet-4-5"]` the family is still routed, since its alias moves to the newest permitted version, but the mod never sends `claude-sonnet-5-5` in place of the alias. A deny entry that names a family with no version (`opus`, `claude-opus`) blocks the family. One with a version (`claude-opus-5-5`, `sonnet[1m]`) blocks that version only, since Claude Code moves an alias off a denied version to the newest permitted one. Blocked models are not picked. When the settings leave no registry model open, the call runs as it is, even above a `max_tier` cap, and the ledger says why. If a model is substituted anyway, two substitutions take it out of rotation.

### Third-party providers

Routing uses the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves for your provider. An alias your provider lacks fails or is substituted, and is then routed around. Some providers resolve aliases to older models ([alias table](https://code.claude.com/docs/en/model-config.md#model-aliases)). On Amazon Bedrock and Google Cloud's Agent Platform (Vertex AI), `sonnet` runs Sonnet 4.5 and `haiku` runs Haiku 4.5; on Microsoft Foundry, `opus` runs Opus 4.6, `sonnet` Sonnet 4.5 and `haiku` Haiku 4.5; on Claude Platform on AWS, `sonnet` runs Sonnet 4.6 and `haiku` Haiku 4.5; on Claude Models on Google Cloud, `haiku` runs Haiku 4.5. Sonnet 4.5 is deprecated and retires on 2026-11-30, replaced by `claude-sonnet-5-5`. That date applies on Anthropic-operated platforms, the Claude API and Microsoft Foundry among them; the partner-operated ones, Amazon Bedrock and Claude Models on Google Cloud (Google managed), set their own ([model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations.md)).

So on those providers the mod starts routed work for those families on the newest model instead: it sends `claude-opus-5-5`, `claude-sonnet-5-5` or `claude-haiku-5-5`, which Bedrock and Agent Platform map to their own IDs and Foundry sends as the deployment name. If the provider refuses it, the call runs on the alias from then on, and the newest model is tried again after a day (then three days, then a week, if it keeps failing). To keep the alias for a family, set its `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` variable to the version you want; `modelOverrides`, or an `availableModels` or `deniedModels` setting that rules the newest version out, keeps the alias too. Where mods don't load, the `Agent` call fallback can only pass an alias.

The main conversation's effort is left alone wherever an effort change re-reads the context uncached: on Bedrock (including its Mantle endpoint) and Vertex, with `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` set, with prompt caching turned off (`DISABLE_PROMPT_CACHING` or the variant for the session model's family), and on a session model it knows isn't in `main.effort_keeps_cache` (Fable 5.1 or later, Opus 5.5, Sonnet 5.5, Haiku 5.5). It is also left alone on Foundry and behind `ANTHROPIC_BASE_URL`, where the router can't confirm the cache survives. The prompt-caching docs don't list Foundry among the places where an effort change drops the cache, and through a gateway what stays cached "depends on how the gateway handles the `cache_control` markers" (https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level, https://code.claude.com/docs/en/prompt-caching.md#where-the-cache-lives). When the model isn't known, as after `/clear`, effort is raised as usual.

### Versions

Needs Claude Code 2.1.329 or later. The manifest's `network` field, which lists the hosts the daily check reaches, needs 2.1.328 (https://code.claude.com/docs/en/plugins/manifest-reference.md), and 2.1.329 fixed the Agent tool's `effort` parameter being ignored for named teammates (https://code.claude.com/docs/en/changelog, 2.1.329), which is how the fallback path sets a teammate's effort. The mod reads its hook environment with `$.process.hookEnv()`, which 2.1.326 added (https://code.claude.com/docs/en/plugins/mods/reference.md). That one floor covers the older version numbers the plugin's features name, so none of them is a separate requirement: mods (2.1.287), per-call effort (2.1.292), the `skill.prompt` event the slash-skill raise reads (2.1.299) and the `engines` check (2.1.323) all came in earlier builds (https://code.claude.com/docs/en/plugins/manifest-reference.md). The mod was tested live on 2.1.330: subagents, workflow agents, agent-team teammates (in-process, in an interactive session), plan mode, effort per request, effort only under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, the spend log, a hard skill typed as a slash command, and a background agent's `SubagentHandback` in auto mode.

### Operating systems

Installed and run live on macOS. The unit tests also run on Linux and Windows in CI.

### Failure handling

Every hook and every mod hook fails open. A bug in this plugin can cost routing but can't block a tool call, a spawn or a session. State files are written atomically, and under a lock when it can be had within two seconds, so agents started together don't overwrite each other's records. A write that would wait longer goes ahead without the lock, so a hook never hangs, and can then lose one update.

## Update and uninstall

- Update: run `claude plugin update model-router@claude-code-model-router` in your shell, or choose **Update now** for the plugin on the **Installed** tab of `/plugin`. Auto-update is off by default for third-party marketplaces like this one ([Claude Code docs](https://code.claude.com/docs/en/plugins/install.md)).
- Pause it: set `routing_mode` to `off` in `/config`. It then changes nothing and only keeps its ledger.
- Remove it: `claude plugin uninstall model-router@claude-code-model-router`, or `/plugin uninstall model-router` in a session. Removing the last install also deletes the plugin's data directory, with its ledger and what it learned, unless you pass `--keep-data` ([CLI reference](https://code.claude.com/docs/en/plugins/cli-reference.md)). Your own `~/.claude/model-router.json` and any project's `.claude/model-router.json` stay where they are.
