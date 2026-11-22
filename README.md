# Claude Code Model Router: autonomous multi-model orchestration plugin for Claude Code

Claude Code Model Router is a Claude Code plugin for autonomous multi-model orchestration. Whenever something in your session starts a subagent, the plugin sets the Claude model and reasoning effort that fit the subtask: Haiku for lookups, Sonnet for everyday implementation, Opus for design and review, Fable for the hardest problems. It works with any plugin, skill or agent, and you never pick a model by hand.

What this Claude Code model router does:

- Sets the model and effort on every `Agent` tool call. The hook keeps the agent the caller chose and changes only those two fields.
- Routes built-in agents, your own project and user agents, and agents from other installed plugins the same way. An agent that names its own model keeps it.
- Treats models as data. A registry ranks each model by strength, speed and cost, and the router works out which of them your setup can use from your settings, from the model each agent really ran on, and from failures.
- Leans toward quality, speed or cost with one `priority` option, and caps the strongest model allowed with another.
- Moves a level of work to a stronger model when routed calls at that level keep being redone on one.
- Asks for a test run after code changes, and for a stronger model's check when a hard request was answered by a weak session model alone.
- Checks the official changelog, docs index and model list once a day and reports releases, docs changes, new models and removed ones.

## Install

In Claude Code:

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

That one-step form needs Claude Code 2.1.275 or later. On older versions, add the marketplace first:

```text
/plugin marketplace add crisnahine/claude-code-model-router
/plugin install model-router@claude-code-model-router
```

The plugin's own name is `model-router`, so its commands start with `/model-router:`.

Requirements: Claude Code 2.1.270 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. Without Python the plugin stays out of the way: its hooks do nothing and its skills say why. The daily update check needs network access to `code.claude.com`.

To try it from a clone without installing, run `claude --plugin-dir ./plugins/model-router`.

There is nothing to configure. Claude model routing is on from the first session.

## How it works

```text
any Agent call ──► profile the subtask ──► select a model ──► rewrite model + effort ──► agent runs
(any plugin,        the brief's wording     cheapest that        on the call itself,       as chosen
 skill or agent)    and the agent's name    is strong enough,    agent unchanged
                                            by priority, cap,
                                            availability
                          ▲                        ▲
                     feedback loop           model registry
                 (levels redone stronger   (strength, speed, cost;
                     get raised)            what really runs here)
```

### 1. Profile the subtask

Each call is scored from its own brief. The profile looks for risk (security, data, migrations, public APIs), scope (several modules or services) and ambiguity (design, root cause, trade-offs). It also checks whether the subtask changes or reasons about something or only reads, and what the agent's name says the agent is for: a reviewer or an architect needs more than a search agent. The result is one of four levels.

| Level | Typical subtask | Model (balanced) | Effort |
| :- | :- | :- | :- |
| lookup | Find, list, summarize, run a command | Haiku | low |
| standard | Implement to a clear spec, write tests | Sonnet | unchanged |
| complex | Design, review, risky or cross-module change | Opus | high |
| hardest | Ambiguous, multi-system and risky at once | Fable | xhigh |

### 2. Select a model

The registry in `config/models.json` gives each model a relative strength, speed and cost. With `balanced` priority the router picks the cheapest model strong enough for the level. `quality` goes one step stronger. `fast` goes one step lighter and takes the quickest model that qualifies. The choice is limited to models inside your cap, permitted by your `availableModels` and `deniedModels` settings, and known to work.

### 3. Rewrite the call

The hook returns the same call with `model` and `effort` set, plus one line that tells Claude which model was chosen and why. Claude Code then runs the call as if Claude had written it that way. If the agent would run on that model anyway, the hook returns nothing.

The plugin never approves a tool call. Your permission rules and auto mode review every agent spawn exactly as they would without it.

### 4. Learn from what happened

The model each agent really ran on is recorded. A model is routed around for a day, and then retried, after two calls on it fail with a model error, run as a different model, or are rejected by the `Agent` tool. If the trouble comes back, the wait grows to three days and then a week.

When Claude redoes a routed call on a stronger model, that counts as a vote that the level was routed too low. Enough votes raise the level by one step. `/model-router:models` shows all of this and can reset it.

### Who decides the model

In order, strongest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` in your environment. Every subagent is pinned, so the router does nothing.
2. A model named on the call itself, by Claude or by a skill's instructions. If you ask for a model in your prompt ("use opus for this"), Claude is reminded to pass it on the calls for that request.
3. A per-agent rule in your `~/.claude/model-router.json` or the project's `.claude/model-router.json`.
4. The agent's own `model` field, unless you set `agent_pins` to `override`.
5. `CLAUDE_CODE_SUBAGENT_MODEL`, your default for agents without a model.
6. The router's pick.

Whatever wins is then lowered, never raised, to fit your cap and what is available.

## Role agents and `/model-router:ship`

The plugin ships seven agents for work that no better-fitting agent covers: `model-router:scout`, `verifier` and `docs-researcher` on Haiku, `implementer` on Sonnet, `architect` and `reviewer` on Opus, and `deep-solver` on Fable. They are ordinary agents with their own models, and their calls go through the same router as every other agent's.

`/model-router:ship <task or issue>` runs a full loop with them: understand, plan, build, verify, review, fix, and commit on a branch. It stops before pushing, merging or deploying.

## Commands

| Command | What it does |
| :- | :- |
| `/model-router:ship <task>` | Plans, builds, verifies and reviews a change end to end |
| `/model-router:route <task>` | Explains which model a task would get and why, and how to split it |
| `/model-router:models` | Lists the models in use, what each really runs as, what is capped or unavailable, and what was learned. `reset` clears it |
| `/model-router:usage [days]` | Shows what ran where: agents, levels, models and tokens |
| `/model-router:refresh` | Reviews new Claude Code releases, docs changes and model changes |

## Configuration

Options, in `/config` under the plugin:

| Option | Default | Meaning |
| :- | :- | :- |
| `routing_mode` | `auto` | `auto` sets model and effort on every agent call. `advise` only tells Claude the estimate. `off` does nothing but keep the ledger |
| `priority` | `balanced` | `balanced`, `quality` or `fast` |
| `max_tier` | none | The strongest model subagents may run on. Anything stronger is lowered to it |
| `agent_pins` | `respect` | `respect` keeps the model an agent names for itself. `override` routes those agents by task too |
| `update_check` | on | The daily check of the changelog, docs index and model list |
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

These limits come from what Claude Code lets a plugin do.

- It can't switch the model of the main conversation. When a request looks harder than the session's model, Claude is told to hand the hard thinking to a strong subagent, and is asked once before finishing if it didn't.
- It only sees work that goes through the `Agent` tool. A skill that runs in its own fork, workflow agents and agent teams take their model from their own settings.
- It leaves alone any agent whose definition it can't read, such as an agent defined on the command line or by a plugin loaded with `--plugin-dir`. That agent may have a model its author chose. Set `agent_pins` to `override` to route those too.
- It doesn't judge answers. The profile reads the wording of the brief and the agent's name, never the code, so it is an estimate. Claude can overrule it on any call, and the feedback loop corrects levels that keep proving too low.
- It doesn't route to a new model family on its own. A new alias in the docs is reported within a day, and it is routed to once it has an entry under `models` and your Claude Code accepts the alias on agent calls.
- It does nothing when hooks are switched off, by `disableAllHooks` or by `allowManagedHooksOnly` in a managed setup.
- It doesn't know prices. Strength, speed and cost in the registry are relative ranks. Dollar totals are in `/usage` and `/cost`.

## Compatibility

Other plugins: the router never changes which agent runs and never refuses a call to steer it, so it has nothing to conflict over. Another plugin's hook that denies or asks about a call still applies. If another plugin's hook rewrites the same call, Claude Code decides which rewrite is kept.

Permission modes: tested in default mode and in auto mode, both headless and in an interactive terminal. Claude Code's documentation says auto mode can refuse a rewritten call once so that it can review the change. Claude then repeats the call as rewritten and it runs.

Refused rewrites: if a rewritten call comes back without having run, the same call is passed through untouched the next time, so a refused rewrite can't loop. The plugin issues one refusal of its own, to protect your cap. When a call asks for a model above the cap and lowering it didn't take, the repeated call is refused with the model to use instead.

Organization allowlists: `availableModels` and `deniedModels` in user, project and managed settings are read, and blocked models are never picked. If a model is substituted anyway, two substitutions take it out of rotation.

Third-party providers: routing uses the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves for your provider. An alias your provider lacks fails or is substituted, and is then routed around.

Versions: tested live on Claude Code 2.1.270 and 2.1.330. Per-call effort needs 2.1.292. Older versions ignore it and still get the model.

Failure handling: every hook catches its own errors and exits 0, so a bug in this plugin can cost routing but can't block a tool call or a session. State files are written atomically and under a lock, so agent calls issued together don't overwrite each other's records.

## Repository layout

```text
plugins/model-router/
  .claude-plugin/plugin.json   manifest and options
  config/models.json           the model registry, levels and role agents
  hooks/hooks.json             SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
                               PostToolUseFailure, PostModelSwitch, Stop
  scripts/modelrouter/         the runtime: registry, profile, router, learn, gates, agents,
                               session, upstream, report, hooks, cli, store (standard library only)
  agents/                      the seven role agents
  skills/                      ship, route, models, usage, refresh
  evals/                       routing evals
tests/                         unit tests, run on Linux, macOS and Windows
compat/baseline.json           the Claude Code version, docs index and model list last reviewed
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the design and the reasons behind it.

## Development

```bash
python3 -m unittest discover -s tests
claude plugin validate --strict .
claude plugin validate --strict plugins/model-router
claude plugin eval plugins/model-router --trust-plugin
```

A daily GitHub workflow compares live Claude Code releases, docs and model aliases with `compat/baseline.json` and opens an issue when something this plugin is built on changes.

## License

MIT
