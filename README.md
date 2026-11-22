# Claude Code Model Router: autonomous multi-model orchestration plugin for Claude Code

Claude Code Model Router is a Claude Code plugin for autonomous multi-model orchestration. Whenever something in your session starts a subagent, a teammate or a workflow agent, the plugin sets the Claude model and reasoning effort that fit the work: Haiku for lookups, Sonnet at high effort for everyday implementation, Opus for design, review and hard coding, Fable for research-grade problems. It also raises the main conversation's effort for a hard request. It works with any plugin, skill or agent, and you never pick a model by hand.

The goal is the lowest cost per finished task at a high quality bar. Each subtask starts on the cheapest model and effort that should do it, and moves up only when the work comes back for a redo.

What this Claude Code model router does:

- Sets the model and effort of every subagent, teammate and workflow agent as it starts, whichever agent, skill or plugin starts it. The agent the caller chose stays the same.
- Raises the main conversation's effort for one turn when the request looks complex or harder. It never lowers what you set, and it leaves the model alone, so the prompt cache is kept.
- Escalates on a redo. When Claude re-issues a call that already came back, that call runs one level higher on the ladder: a stronger model, or more effort when it stays on the same model.
- Treats models as data. A registry ranks each model by strength, speed and cost, and the router works out which of them your setup can use from your settings, from the model each agent ran on, and from failures.
- Logs what each turn cost at list price, main conversation and subagents apart, in `/model-router:usage`.
- Leans toward quality, speed or cost with one `priority` option, and caps the strongest model allowed with another.
- Asks for a test run after code changes, and for a stronger model's check when a hard request was answered by a weak session model alone.
- Checks the official changelog, docs index, model aliases and model deprecations once a day, plus your account's model list when an Anthropic API key is set, and reports releases, docs changes, new models and models it routes to that are retiring.

## Install

In Claude Code:

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

On versions without the one-step form, add the marketplace first:

```text
/plugin marketplace add crisnahine/claude-code-model-router
/plugin install model-router@claude-code-model-router
```

The plugin's own name is `model-router`, so its commands start with `/model-router:`.

Requirements: Claude Code 2.1.292 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. Without Python the plugin stays out of the way: its hooks do nothing and its skills say why. The daily update check needs network access to `code.claude.com` and `platform.claude.com`, and with an Anthropic API key set, `api.anthropic.com` for your account's model list.

To try it from a clone without installing, run `claude --plugin-dir ./plugins/model-router`.

There is nothing to configure. Claude model routing is on from the first session.

## How it works

![Two control paths, one router: engine events reach the mod, settings hooks are the fallback, and both call one Python runtime](docs/assets/two-paths.svg)

The plugin has two parts. A mod (a hooks module, `hooks/router.ts`) hooks Claude Code's own events: `agent.spawn` for every subagent, teammate and workflow agent, `turn.step` for effort on each model request, and `turn.start` and `turn.complete` for the main conversation and the spend log. Settings hooks (`hooks/hooks.json`) do the rest: the checks before a turn ends, availability tracking, and routing through the `Agent` tool call when mods aren't available. Both call the same Python runtime, so one router decides everything.

### 1. Profile the subtask

Each subtask is scored from its own brief. The profile looks for risk (security, data, migrations, public APIs), scope (several modules or services) and ambiguity (design, root cause, trade-offs). It also checks whether the subtask changes or reasons about something or only reads, and what the agent's name says the agent is for: a reviewer or an architect needs more than a search agent. The result is one of five levels.

| Level | Typical subtask | Model (balanced) | Effort |
| :- | :- | :- | :- |
| lookup | Find, list, summarize, run a command | Haiku | low |
| standard | Implement to a clear spec, write tests | Sonnet | high |
| complex | Design, review, risky or cross-module change | Opus | high |
| hardest | Ambiguous, multi-system and risky at once | Opus | xhigh |
| frontier | All of that, with no known approach: research, open-ended investigation | Fable | high |

![The ladder: lookup on Haiku at low, standard on Sonnet at high, complex on Opus at high, hardest on Opus at xhigh, frontier on Fable at high](docs/assets/ladder.svg)

The ladder follows Anthropic's published cost-per-solved-task numbers. Sonnet at its Claude Code default of `medium` scores about 7 points lower on coding than at `high`, so standard work runs at `high`. On the same coding problems Opus 5.5 at `high` came within a point of Fable 5.2 at `high` (95.5% against 96.3%) for under a quarter of the cost, so hard coding stays on Opus, with `xhigh` for the hardest, and Fable is kept for research-grade work.

### 2. Select a model

The registry in `config/models.json` gives each model a relative strength, speed and cost. With `balanced` priority the router picks the cheapest model strong enough for the level. `quality` goes one step stronger. `fast` goes one step lighter and takes the quickest model that qualifies. The choice is limited to models inside your cap, permitted by your `availableModels` and `deniedModels` settings, and known to work.

### 3. Write it on the spawn

The mod sets the model on the agent as it starts and sets its effort on each of its model requests. On setups without mods, the `PreToolUse` hook returns the `Agent` call with `model` and `effort` set instead. If the agent would run on that model and effort anyway, nothing is changed.

The plugin never approves a tool call. Your permission rules and auto mode review every spawn exactly as they would without it.

### 4. Escalate and learn

![Redo escalation: a call routed at standard comes back, the same call again runs at complex, and counts as a vote that standard was too low](docs/assets/redo-escalation.svg)

A call Claude re-issues after its result came back runs one level higher. That is the cheapest policy Anthropic measured for a high pass rate: start low and re-run what fails at a higher setting. That beat starting every task high on both cost and pass rate. Each redo also counts as a vote that the level was routed too low. At least 6 votes that are also at least 30% of the level's calls raise it one step, and a raise that keeps being overruled with a weaker model is undone the same way. Copies of one brief started together are not redos, and a background agent's call counts as finished when the agent stops.

The model each agent ran on is recorded. A model is routed around for a day, and then retried, after two calls on it fail with a model error, run as a different model, or are rejected by the `Agent` tool. If the trouble comes back, the wait grows to three days and then a week. `/model-router:models` shows all of this and can reset it.

### Who decides the model

In order, strongest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` in your environment. Every subagent's model is pinned, so the router sets only effort. The session-start note says so once, because a pinned strong model costs far more on lookups: in a live test, listing a directory on a forced Opus subagent cost about 30 times what the same subagent cost on Haiku ($0.39 against $0.012).
2. A model named on the call itself, by Claude, a skill's instructions or a workflow script. If you ask for a model in your prompt ("use opus for this"), Claude is reminded to pass it on the calls for that request.
3. A per-agent rule in your `~/.claude/model-router.json` or the project's `.claude/model-router.json`.
4. The agent's own `model` field, unless you set `agent_pins` to `override`. Its effort still follows the subtask when the agent sets none.
5. `CLAUDE_CODE_SUBAGENT_MODEL`, your default for agents without a model.
6. The router's pick.

Whatever wins then has to be usable: inside your cap, allowed by your settings and known to work. If it isn't, the strongest usable model below it runs, or the weakest usable one when none is below it. Effort is left alone when the call, the agent, `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` already sets it.

## Role agents and `/model-router:ship`

The plugin ships seven agents for work that no better-fitting agent covers: `model-router:scout`, `verifier` and `docs-researcher` on Haiku, `implementer` on Sonnet at high effort, `architect` and `reviewer` on Opus, and `deep-solver` on Fable. They are ordinary agents with their own models, and their calls go through the same router as every other agent's.

`/model-router:ship <task or issue>` runs a full loop with them: understand, plan, build, verify, review, fix, and commit on a branch. It stops before pushing, merging or deploying.

## Commands

| Command | What it does |
| :- | :- |
| `/model-router:ship <task>` | Plans, builds, verifies and reviews a change end to end |
| `/model-router:route <task>` | Explains which model a task would get and why, and how to split it |
| `/model-router:models [reset] [--json]` | Lists the models in use, what each runs as, what is capped or unavailable, and what was learned. `reset` clears it |
| `/model-router:usage [days] [--json]` | Shows what ran where, and what it cost at list price: agents, levels, models, tokens and dollars |
| `/model-router:refresh` | Reviews new Claude Code releases, docs changes, model changes and retirements |

With `--json`, `models` and `usage` print one JSON object instead of the tables. Its `schema` key names the shape and its version, `model-router.models/1` and `model-router.usage/1`. A new key keeps the version; a key removed, renamed or changed in meaning bumps it. Scripts can run the same commands without Claude: `sh <plugin>/scripts/model-router.sh --data-dir <plugin data dir> usage 30 --json`. The shapes are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#machine-readable-output).

## Configuration

Options, in `/config` under the plugin:

| Option | Default | Meaning |
| :- | :- | :- |
| `routing_mode` | `auto` | `auto` sets model and effort on every agent. `advise` only tells Claude the estimate. `off` does nothing but keep the ledger |
| `priority` | `balanced` | `balanced`, `quality` or `fast` |
| `max_tier` | none | The strongest model subagents may run on. Anything stronger is lowered to it |
| `agent_pins` | `respect` | `respect` keeps the model an agent names for itself. `override` routes those agents by task too |
| `main_effort` | on | Raises the main conversation's effort for a complex or harder request, for that turn. Off where an effort change would re-read the context uncached: Bedrock, Vertex, Foundry, `ANTHROPIC_BASE_URL`, a Claude apps gateway or another third-party provider, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`, and models other than Fable 5.1+, Opus 5.5, Sonnet 5.5 and Haiku 5.5. Turn it off yourself in a HIPAA organization, which the router doesn't detect, or if Claude Desktop reaches Claude through a Claude apps gateway, which it can't tell from a subscription there |
| `update_check` | on | The daily check of the changelog, docs index, model list and deprecations |
| `verify_gate` | on | The two checks before a turn ends |

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

Your own file is trusted in full. A project's file can turn routing down to `advise` or `off`, set rules for agents and add models. It can't turn routing on for someone who switched it off, re-rank a model that ships with the plugin or that you defined, or get any model past your cap.

## What it can't do

These limits come from what Claude Code lets a plugin do, or from choices made to protect quality and cost.

- It doesn't switch the main conversation's model. A mod could, but each model keeps its own prompt cache, so a switch re-reads the whole conversation uncached. It changes the main conversation's effort instead, which keeps the cache on current models, and when a request looks harder than the session's model, Claude is told to hand the hard thinking to a strong subagent and is asked once before finishing if it didn't.
- It leaves forks alone. A fork reads its parent's cache and always runs on its parent's model.
- It leaves alone an agent whose definition it can't read, such as an agent defined on the command line, unless `agent_pins` is `override`. That agent may have a model its author chose. Workflow stages and teammate roles, which the engine makes, are routed.
- It doesn't judge answers. The profile reads the wording of the brief and the agent's name, never the code, so it is an estimate. Claude can overrule it on any call, and redo escalation and the feedback loop correct levels that prove too low.
- It doesn't route to a new model family on its own. A new alias in the docs is reported within a day, and it is routed to once it has an entry under `models`. New versions inside a family need nothing: the router uses aliases, and Claude Code moves each alias to the newest version. When your session's model or an alias still runs an older version than the newest of its family, session start says so: usually a `model` setting naming a full version, or Claude Code waiting for an update.
- It does nothing when hooks are switched off, by `disableAllHooks` or by `allowManagedHooksOnly` in a managed setup. Where an organization blocks user mods, routing falls back to the `Agent` call.
- Spend figures are list prices from the registry's `prices` table. Each model request is priced on its own and a turn's figure is the sum, so Haiku 5.5's higher rate for a prompt over 100K tokens applies to exactly the requests that cross it. Fast mode requests are counted at standard rates, because the usage Claude Code reports doesn't say which requests ran fast. A model with no price is listed as such. Add a missing price under `prices` in your own `~/.claude/model-router.json`; a project's file can't set prices. Your bill is in `/usage` and `/cost`.

## Compatibility

Other plugins: the router never changes which agent runs and never refuses a call to steer it, so it has nothing to conflict over. Another plugin's hook that denies or asks about a call still applies. Another plugin's mod that sets a spawn's model runs in its own order with this one, and the model that ran is what the ledger records.

Permission modes: the mod sets the model after the call was reviewed, so auto mode's review of a changed call doesn't come into it. The `Agent` call fallback was tested in default and auto mode. Claude Code's documentation says auto mode can refuse a rewritten call once so that it can review the change. Claude then repeats the call as rewritten and it runs.

Refused rewrites: on the `Agent` call fallback, if a rewritten call comes back without having run, the same call is passed through untouched the next time, so a refused rewrite can't loop. The plugin issues one refusal of its own, to protect your cap. When a call asks for a model above the cap and lowering it didn't take, the repeated call is refused with the model to use instead.

Organization allowlists: `availableModels` and `deniedModels` are read the way Claude Code reads them. A managed `availableModels` list applies alone; otherwise the lists in user, project and local settings are combined. `deniedModels` counts only in managed settings. Only the file-based managed policy is read: `managed-settings.json` and the `*.json` files in `managed-settings.d/` beside it, merged with their lists combined. MDM and server-managed policies are not read. An allow entry stands for its family, so `claude-opus-4-6` permits `opus`. A deny entry blocks a family only when it names it, since Claude Code moves an alias off a denied version to the newest permitted one. Blocked models are not picked, unless the settings leave no registry model open, in which case they are ignored. If a model is substituted anyway, two substitutions take it out of rotation.

Third-party providers: routing uses the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves for your provider. An alias your provider lacks fails or is substituted, and is then routed around. The main conversation's effort is left alone wherever an effort change re-reads the context uncached: on Bedrock, Vertex and Foundry, behind `ANTHROPIC_BASE_URL`, with `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` set, and on a session model it knows is older than Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5. When the model isn't known, as after `/clear`, effort is raised as usual.

Versions: needs Claude Code 2.1.292 for per-call effort. Mods need 2.1.287 and were tested live on 2.1.330: subagents, workflow agents, agent-team teammates (in-process, in an interactive session), effort per request, effort only under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, and the spend log.

Failure handling: every hook and every mod hook fails open. A bug in this plugin can cost routing but can't block a tool call, a spawn or a session. State files are written atomically and under a lock, so agents started together don't overwrite each other's records.

## Repository layout

```text
plugins/model-router/
  .claude-plugin/plugin.json   manifest and options
  config/models.json           the model registry, levels, prices and role agents
  hooks/router.ts              the mod: agent.spawn, turn.start, turn.step, turn.complete
  hooks/hooks.json             the mod, plus SessionStart, UserPromptSubmit, PreToolUse,
                               PostToolUse, PostToolUseFailure, PostModelSwitch,
                               SubagentStop, Stop
  scripts/modelrouter/         the runtime: registry, profile, router, learn, gates, agents,
                               session, upstream, report, hooks, cli, store (standard library only)
  agents/                      the seven role agents
  skills/                      ship, route, models, usage, refresh
  evals/                       routing evals
tests/                         unit tests, run on Linux, macOS and Windows
compat/baseline.json           the Claude Code version, docs index, model list and model status last reviewed
docs/research/                 the primary-source research the design rests on
docs/README.md                 index of the project's documents: traceability, readiness, roadmap, test results
tools/traceability.py          counts the requirement statuses in docs/TRACEABILITY.md
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the design and the reasons behind it.

## Development

```bash
python3 -m unittest discover -s tests
node --experimental-strip-types --test "tests/mod/*.test.ts"
claude plugin validate --strict .
claude plugin validate --strict plugins/model-router
claude plugin eval plugins/model-router --trust-plugin
```

A daily GitHub workflow compares live Claude Code releases, docs, model aliases and model status with `compat/baseline.json` and opens an issue when something this plugin is built on changes.

## License

MIT
