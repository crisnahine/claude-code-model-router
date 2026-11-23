# Claude Code Model Router: the right Claude model for every subagent

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="docs/assets/hero-phone.svg">
    <img src="docs/assets/hero.svg" width="100%" alt="Model Router, a Claude Code plugin. An agent call to implement a function is profiled as standard and runs on Sonnet at medium effort. Its result comes back for a redo, so the same call climbs one level to complex, Sonnet at high effort, and is solved. The ladder runs lookup on Haiku at low, standard on Sonnet at medium, complex on Sonnet at high, hardest on Opus at high and frontier on Fable at high.">
  </picture>
</p>

<p align="center">
  <a href="CHANGELOG.md"><img src="docs/assets/badge-version.svg" height="28" alt="version 0.1.0"></a>
  <a href="#install-the-claude-code-plugin"><img src="docs/assets/badge-claude-code.svg" height="28" alt="Claude Code 2.1.292 or later"></a>
  <a href="#install-the-claude-code-plugin"><img src="docs/assets/badge-python.svg" height="28" alt="Python 3.9 or later"></a>
  <a href="CONTRIBUTING.md#rules-for-a-change"><img src="docs/assets/badge-dependencies.svg" height="28" alt="dependencies: none, standard library only"></a>
  <a href="LICENSE"><img src="docs/assets/badge-license.svg" height="28" alt="license: Apache-2.0"></a>
</p>

<p align="center">
  <a href="#install-the-claude-code-plugin"><b>Install</b></a> &nbsp;&middot;&nbsp;
  <a href="#quick-start"><b>Quick start</b></a> &nbsp;&middot;&nbsp;
  <a href="#how-it-works-picking-a-subagent-model"><b>How it works</b></a> &nbsp;&middot;&nbsp;
  <a href="#configuration"><b>Options</b></a> &nbsp;&middot;&nbsp;
  <a href="#does-it-reduce-claude-code-cost-the-measured-result"><b>Results</b></a> &nbsp;&middot;&nbsp;
  <a href="docs/FAQ.md"><b>Questions</b></a> &nbsp;&middot;&nbsp;
  <a href="docs/README.md"><b>Docs</b></a>
</p>


**Model Router (`model-router`) is a Claude Code plugin that sets the model and reasoning effort of every agent Claude Code starts, choosing the cheapest Claude model that should do the job and moving a call one level up when its work comes back for a redo.** It covers subagents from any plugin, skill or agent, agent-team teammates and workflow stages, and you never pick a model by hand ([how it works](docs/ARCHITECTURE.md)).

Without it, a subagent that names no model runs on the same model as the conversation that starts it ([Claude Code docs](https://code.claude.com/docs/en/sub-agents.md#choose-a-model)), so on an Opus session a file listing runs on Opus too. One model for every agent, set with `CLAUDE_CODE_SUBAGENT_MODEL`, either puts lookups on a strong, expensive model or puts hard design work on a weak one. Model Router does autonomous multi-model orchestration instead: each call gets the model and effort its own brief needs.

## What it does

- Sets the model and effort of every subagent, teammate and workflow agent as it starts. The agent the caller chose stays the same.
- Runs a call one level higher when Claude re-issues it after its result came back, and raises a level that keeps needing redos.
- Logs what each turn cost at list price, main conversation and subagents apart, in `/model-router:usage`.
- Asks once, before a turn ends, for a stronger agent's check when a weak session model answered a hard request alone.
- Checks the Claude Code changelog, docs and model retirements once a day and reports what affects routing.

## Install the Claude Code plugin

In Claude Code:

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

Or from your shell: `claude plugin install model-router --marketplace crisnahine/claude-code-model-router`.

Requirements: Claude Code 2.1.292 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. Without Python the plugin stays out of the way: its hooks do nothing and its skills say why.

There is nothing to configure. Claude model routing is on from the first session. To try it from a clone without installing, run `claude --plugin-dir ./plugins/model-router`.

## Quick start

1. Start a new session. The plugin's commands start with `/model-router:`.
2. Check what a task would get: `/model-router:route list the files under src`. It names the level, model and effort, and why. This is how the router reads three briefs (output of its `explain` command, which the skill runs):

   ```text
   > List the files under src and say which ones import requests
   Level: lookup (no risk, scope or ambiguity wording).
   Model: haiku, effort: low.

   > Implement parse_date in utils.py to the spec in the docstring, with tests
   Level: standard (no risk, scope or ambiguity wording).
   Model: sonnet, effort: medium.

   > Review the auth middleware change for security holes and race conditions across the api and worker services
   Level: complex (risk, scope).
   Model: sonnet, effort: high.
   ```

3. Work as you always do. Every agent call is routed as it starts.
4. After some work, `/model-router:usage` shows what ran where and what it cost at list price, and `/model-router:models` shows the models in use and anything capped, unavailable or learned.

To change how it routes, open `/config` and find the plugin's options ([Configuration](#configuration)).

## How it works: picking a subagent model

Each subtask is scored from its own brief: risk (security, data, migrations, public APIs), scope (several modules or services), load, ambiguity (design, a choice between approaches, root cause), whether it changes something or only reads, and what the agent's name says it is for. The result is one of five levels:

| Level | Typical subtask | Model (balanced) | Effort |
| :- | :- | :- | :- |
| lookup | Find, list, summarize, run a command | Haiku | low |
| standard | Implement to a clear spec, write tests | Sonnet | medium |
| complex | Design, review, risky or cross-module change | Sonnet | high |
| hardest | Ambiguous, multi-system and risky at once | Opus | high |
| frontier | All of that, with no known approach: research, open-ended investigation | Fable | high |

A call Claude re-issues after its result came back runs one level higher: a stronger model, or more effort on the same one. Starting low and re-running what fails follows Anthropic's own measurement: low effort with failures re-run at high solved about 97% of a coding benchmark for $0.17 a task, against 95.3% for $0.29 with every task at high ([research](docs/research/model-control.md), Q7).

<a id="who-decides-the-model"></a><a id="the-checks-before-a-turn-ends"></a>Your choices come first. Inside your `max_tier` cap, a model named on the call, a per-agent rule or the agent's own `model` field wins over the router's pick. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and `CLAUDE_CODE_SUBAGENT_MODEL` leave the model as you set it ([who decides the model](docs/GUIDE.md#who-decides-the-model)). The checks before a turn ends are in the [guide](docs/GUIDE.md#the-checks-before-a-turn-ends), and the full design is in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Does it reduce Claude Code cost? The measured result

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="docs/assets/benchmark-phone.svg">
    <img src="docs/assets/benchmark.svg" width="100%" alt="Cost per solved task by task class, from docs/BENCHMARKS.md, 10 runs per class and configuration. On an Opus 5.5 session the router cost less than Claude Code's defaults in seven classes, with every run solved for both, for example lookup $0.0630 against $0.0989 and research $0.1233 against $0.1697. The difference is outside the noise in those seven (PASS), and design is level, $0.1885 against $0.1878 (PARTIAL). On a Sonnet session the router is cheapest only in lookup ($0.0363 against $0.0479, PASS) and costs more than the best baseline in the other seven (FAIL), from 7% more in implementation and root cause to 63% more in design.">
  </picture>
</p>

On an Opus 5.5 session, the default model for every account type but Microsoft Foundry ([model configuration](https://code.claude.com/docs/en/model-config.md)), the router cost less per solved task than Claude Code's defaults in 7 of 8 task classes, 18 to 36% less, with all 80 runs solved for both. Those 7 differences are outside the noise (PASS). Design is level, $0.189 against $0.188 (PARTIAL). Over all 80 runs it cost $0.119 per solved task against $0.152.

On a Sonnet session the router is behind. It is cheapest only in lookup (24% less than the defaults, PASS), and in the other 7 classes it costs 7 to 63% more per solved task than the best baseline at the same pass rate (FAIL). [Release readiness](docs/RELEASE_READINESS.md) says why, and the [roadmap](docs/ROADMAP.md) says what comes next.

Both ran the routing code at commit `780ee86`, 10 runs per class and configuration. [BENCHMARKS.md](docs/BENCHMARKS.md) has every table, the raw data and the commands to rerun it.

## Commands

| Command | What it does |
| :- | :- |
| `/model-router:ship <task>` | Plans, builds, verifies, reviews and commits a change on a branch, and stops before pushing, merging or deploying |
| `/model-router:route <task>` | Explains which model a task would get and why, and how to split it |
| `/model-router:models [reset] [--json]` | Lists the models in use, what each runs as, what is capped or unavailable, and what was learned. `reset` clears it |
| `/model-router:usage [days] [--json]` | Shows what ran where, and what it cost at list price: agents, levels, models, tokens and dollars |
| `/model-router:refresh` | Reviews new Claude Code releases, docs changes, model changes and retirements |

The plugin also ships seven role agents for work no better-fitting agent covers: `model-router:scout`, `verifier`, `docs-researcher`, `implementer`, `architect`, `reviewer` and `deep-solver` ([what each is for](docs/GUIDE.md#role-agents)). `models` and `usage` take `--json` for scripts ([output shapes](docs/GUIDE.md#commands)).

## Configuration

Options, in `/config` under the plugin:

| Option | Default | Meaning |
| :- | :- | :- |
| `routing_mode` | `auto` | `auto` sets model and effort on every agent. `advise` only tells Claude the estimate. `off` does nothing but keep the ledger |
| `priority` | `balanced` | `balanced`: the cheapest model strong enough. `quality`: one step stronger. `fast`: one step lighter, and the quickest that qualifies |
| `max_tier` | none | The strongest model subagents may run on: `haiku`, `sonnet`, `opus` or `fable`. Anything stronger is lowered to it |
| `agent_pins` | `respect` | `respect` keeps the model an agent names for itself. `override` routes those agents by task too |
| `main_effort` | off | When on, raises the main conversation's effort for one turn on a complex or harder request, and never lowers it. Leave it off in a HIPAA organization, or if Claude Desktop reaches Claude through a Claude apps gateway. It turns itself off where an effort change would re-read the context uncached ([where](docs/GUIDE.md#configuration)) |
| `update_check` | on | The daily check of the changelog, docs index, model list and deprecations |
| `verify_gate` | off | Asks for a test run before a turn ends when code changed and nothing checked it |
| `strong_agent_gate` | on | Asks for a stronger agent's check before a turn ends when a weak session model answered a hard request alone |

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

A project's file can raise any level's model and effort, so a repository you clone can make its subagents cost more. It can't get any model past your `max_tier`. Every key, the trust rules and the file's version are in the [guide](docs/GUIDE.md#configuration).

## What it can't do

- It doesn't switch the main conversation's model, because a switch re-reads the whole conversation uncached. It can raise that turn's effort instead (`main_effort`, off by default).
- It reads the brief's wording and the agent's name, never the code, so a level is an estimate. Claude can overrule it on any call, and a redo moves it up.
- The profile reads English wording. A brief mostly in another language starts at standard.
- It leaves forks alone, and agents whose definition it can't read, such as one defined on the command line.
- It routes a new model family only once the registry has an entry for it. New versions inside a family need nothing.
- It does nothing when hooks are switched off (`disableAllHooks`, `allowManagedHooksOnly`). Spend figures are list prices; your bill is in `/usage` and `/cost`.

[Every limit, and why](docs/GUIDE.md#what-it-cant-do).

## Compatibility

- Other plugins, skills and agents: their agents are routed too. The router never changes which agent runs and never refuses a call to steer it. Another plugin's hook that denies or asks about a call still applies.
- Permissions: the plugin never approves a tool call. Your permission rules and auto mode review every spawn as they would without it.
- Amazon Bedrock, Google Cloud's Agent Platform (Vertex AI) and Microsoft Foundry: routing uses the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves for your provider. Where an alias resolves to an older model, the mod sends the family's newest model ID, and falls back to the alias if the provider refuses it ([details](docs/GUIDE.md#third-party-providers)).
- Versions and status: Claude Code 2.1.292 or later. Tested live on 2.1.330 on macOS, and the unit tests also run on Linux and Windows in CI. Version 0.1.0; what is still open is in [release readiness](docs/RELEASE_READINESS.md).
- Failure: every hook and mod hook fails open. A bug can cost routing, but it can't block a tool call, a spawn or a session.

<a id="security"></a>
## Privacy and security

Model Router runs on your machine with your user permissions, as every plugin's hooks and mods do ([Claude Code: plugin security](https://code.claude.com/docs/en/plugins/security.md)): settings hooks through `sh` and Python, and a mod, `hooks/router.ts`. It reads each request and agent brief there to pick a level, keeps its state in its own data directory, and sends no prompt or code anywhere. Its only network traffic is the daily update check to `code.claude.com` and `platform.claude.com`, plus `api.anthropic.com` for your account's model list when an Anthropic API key is set. Turn `update_check` off to stop it. To see every hook, agent and skill it installs before you install it, run `claude --plugin-dir ./plugins/model-router plugin details model-router` from a clone. [docs/SECURITY.md](docs/SECURITY.md) has the threat model, what is stored and the API key path. To report a problem, read its "Reporting a problem" section first: there is no private channel yet, so never put details in a public issue.

## Update and uninstall

- Update: run `claude plugin update model-router@claude-code-model-router` in your shell, or choose **Update now** for the plugin on the **Installed** tab of `/plugin`. Auto-update is off by default for third-party marketplaces like this one ([Claude Code docs](https://code.claude.com/docs/en/plugins/install.md)).
- Pause it: set `routing_mode` to `off` in `/config`. It then changes nothing and only keeps its ledger.
- Remove it: `claude plugin uninstall model-router@claude-code-model-router`, or `/plugin uninstall model-router` in a session. Removing the last install also deletes the plugin's data directory, with its ledger and what it learned, unless you pass `--keep-data`. Your own `~/.claude/model-router.json` and any project's `.claude/model-router.json` stay where they are.

## Documentation

[docs/FAQ.md](docs/FAQ.md) answers the questions people ask first, each in its first sentence: what Model Router is, how it picks a model, what it costs, whether quality drops, how to turn parts of it off, Bedrock, Vertex and Foundry, what it stores on your machine, and how it differs from setting one model for every agent.

| Page | Answers |
| :- | :- |
| [Guide](docs/GUIDE.md) | How do I configure it for my setup? |
| [Architecture](docs/ARCHITECTURE.md) | How do two paths share one router? |
| [Benchmarks](docs/BENCHMARKS.md) | What does a solved task cost, with and without it? |
| [Security](docs/SECURITY.md) | What reaches the network, and who can steer it? |
| [Performance](docs/PERFORMANCE.md) | What does the router cost in time and disk? |
| [Roadmap](docs/ROADMAP.md) | What gets built next, and why first? |
| [Changelog](CHANGELOG.md) | What changed in each release? |

Every other page, including the glossary, test results, traceability and release readiness, is in the [documentation index](docs/README.md). Bugs and questions go to [GitHub issues](https://github.com/crisnahine/claude-code-model-router/issues). To work on the plugin, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).

Copyright 2026 Cris Nahine.

Model Router is an independent project, not made or endorsed by Anthropic.
