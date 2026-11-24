# Claude Code Model Router: the right Claude model for every subagent

<p align="center">
  <a href="CHANGELOG.md"><img src="docs/assets/badge-version.svg" height="28" alt="version 0.3.0"></a>
  <a href="#install-the-claude-code-plugin"><img src="docs/assets/badge-claude-code.svg" height="28" alt="Claude Code 2.1.329 or later"></a>
  <a href="#install-the-claude-code-plugin"><img src="docs/assets/badge-python.svg" height="28" alt="Python 3.9 or later"></a>
  <a href="CONTRIBUTING.md#rules-for-a-change"><img src="docs/assets/badge-dependencies.svg" height="28" alt="dependencies: none, standard library only"></a>
  <a href="LICENSE"><img src="docs/assets/badge-license.svg" height="28" alt="license: Apache-2.0"></a>
</p>

<p align="center">
  <a href="#install-the-claude-code-plugin"><b>Install</b></a> &nbsp;&middot;&nbsp;
  <a href="#quick-start"><b>Quick start</b></a> &nbsp;&middot;&nbsp;
  <a href="#how-it-works-picking-a-subagent-model"><b>How it works</b></a> &nbsp;&middot;&nbsp;
  <a href="#built-on-the-claude-code-docs"><b>By the docs</b></a> &nbsp;&middot;&nbsp;
  <a href="#configuration"><b>Options</b></a> &nbsp;&middot;&nbsp;
  <a href="#benchmarks"><b>Benchmarks</b></a> &nbsp;&middot;&nbsp;
  <a href="docs/FAQ.md"><b>Questions</b></a> &nbsp;&middot;&nbsp;
  <a href="docs/README.md"><b>Docs</b></a>
</p>


**Model Router (`model-router`) is a Claude Code plugin that keeps the model and reasoning effort Claude names for every agent Claude Code starts inside your limits, fills in the effort when only a model is named, and runs a call Claude marks as a redo with more effort or on a stronger model.** It covers subagents from any plugin, skill or agent, agent-team teammates and workflow stages, and you never pick a model by hand ([how it works](docs/ARCHITECTURE.md)).

Without it, a subagent that names no model runs on its conversation's model ([Claude Code docs](https://code.claude.com/docs/en/sub-agents.md#choose-a-model)), so on an Opus session a file listing runs on Opus too. One model for every agent, set with `CLAUDE_CODE_SUBAGENT_MODEL`, puts lookups on an expensive model or hard design work on a weak one. With Model Router, a note at session start asks Claude, which has read the work, to name the model and effort each call needs, and the plugin holds what it names to your cap and to the models that work here.

<p align="center">
  <a href="https://crisnahine.github.io/claude-code-model-router/assets/video.mp4"><img src="docs/assets/video-play.jpg" width="720" alt="Play the 22-second video, made for the earlier design: on an Opus session, four agent calls run on Haiku, Sonnet, Opus and Fable instead of all on Opus, and a redo moves a call from Sonnet to Opus."></a><br>
  <sub><a href="https://crisnahine.github.io/claude-code-model-router/assets/video.mp4"><b>Watch it in 22 seconds</b></a>, with sound. It shows the earlier design: now Claude names each call's model and effort, a redo raises effort on the same model before it moves up, and no benchmark has measured what this design costs. Music: Sascha Ende, ende.app (CC BY 4.0).</sub>
</p>

## What it does

- Keeps the model of every subagent, teammate and workflow agent inside your cap as it starts, and fills in the effort from the model's level when only a model is named. A call that nothing names a model for runs on its parent's model, within the same limits, unless a rule's `level` raises it or it is a teammate on a parent weaker than Sonnet. The agent the caller chose stays the same.
- Runs a call Claude marks "Redo:" that names no model or effort one effort step higher on the same model, or on the next stronger model once the run it redoes was at `high` or above, or when the step up would gain nothing because your environment sets the effort or `maxEffortLevel` holds it. `/model-router:usage` counts, per level, the redos that climbed from a finished run on record.
- Asks once, before a turn ends, for the work's own check when a project file changed through Edit, Write or NotebookEdit and no check command ran since: tests for code, a recompute for figures, a reread for a document. It stays quiet when routing is off, while background work runs, and when it can't read the transcript.
- Shows in `/model-router:usage` what each subagent ran on and, where the plugin's mod runs, Claude Code's own cost estimate for it (none for workflow agents). It makes no network call of its own.

## Built on the Claude Code docs

Model Router is built on the extension points Claude Code documents (the plugin manifest, hooks, the mods API, skills and subagents), and follows its rules on subagent models, the prompt cache and failing hooks:

- It keeps the docs' subagent model order: a model on the call, the agent's own model, `CLAUDE_CODE_SUBAGENT_MODEL`, then the parent's model ([sub-agents](https://code.claude.com/docs/en/sub-agents.md#choose-a-model)). The one addition is yours: a per-agent rule in `model-router.json` comes before the agent's own model (a project's rule only when its model isn't weaker). It lowers a model to your cap, moves one that isn't usable here to the next stronger usable model, raises one to a rule's level and fills in the effort, and picks a model itself only to start a teammate on a weak parent on Sonnet and to climb a call Claude marks as a redo.
- It never switches the main conversation's model, because a switch re-reads the history with no cache hits ([prompt caching](https://code.claude.com/docs/en/prompt-caching.md#switching-models)).
- Its hooks fail open: a bug never blocks a tool call, a spawn or a session ([exit codes](https://code.claude.com/docs/en/hooks.md#exit-code-output)).

The design follows Claude Code's own advice to put simple subagents on Haiku ([best practices](https://code.claude.com/docs/en/best-practices.md#know-what-subagents-cost)): the plugin's search and check agents carry Haiku in their definitions, and the session-start note asks Claude to name Haiku for finding, reading and listing. No benchmark has measured this design yet ([benchmarks](#benchmarks)). [Built on the docs](docs/CLAUDE_CODE_DOCS.md) lists every rule, its doc page and its test. It also lists [where the plugin goes past the docs](docs/CLAUDE_CODE_DOCS.md#where-it-goes-past-the-docs), such as the `agent.spawn` fields the mod reads that only the type files Claude Code writes for each build list.

## Install the Claude Code plugin

In Claude Code:

```text
/plugin install model-router --marketplace crisnahine/claude-code-model-router
```

In a session this opens the plugin in the `/plugin` panel, where you pick a scope (you in every project, everyone in this repository, or you in this repository only). In your shell, `claude plugin install model-router --marketplace crisnahine/claude-code-model-router` installs at user scope unless you pass `--scope project` or `--scope local` ([install docs](https://code.claude.com/docs/en/plugins/install.md#add-a-marketplace-and-install-in-one-command)).

Requirements: Claude Code 2.1.329 or later, `sh`, and Python 3.9 or later as `python3` or `python` on your `PATH`. Without Python its hooks do nothing and its skills say why. The plugin's `engines` field holds older versions off, and Claude Code reads that field only from 2.1.323 on ([manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference.md)), so update Claude Code first.

Nothing has to be set: the three options in `/config` under the plugin start at their defaults ([options](docs/GUIDE.md#configuration)), and Claude model routing is on from the first session. To try it from a clone, run `claude --plugin-dir ./plugins/model-router`.

## Quick start

1. Start a new session. The plugin's commands start with `/model-router:`.
2. Check what a call would run on: `/model-router:route` shows the policy, the level each model stands for and the effort a call gets when it names none.
3. Work as you always do. Every agent call is checked as it starts.
4. Later, `/model-router:usage` shows what ran where and, where the plugin's mod runs, Claude Code's cost estimate for each subagent, and `/model-router:models` shows the models in use and anything capped, unavailable or routed around. To change how it routes, see [Configuration](#configuration).

## How it works: picking a subagent model

The session-start note asks Claude to name the model and effort on each agent call, by the work. The router never scores the brief or picks a model from it: it reads the prompt only to hash it into a key that recognizes the same call sent again. Each model stands for the lowest of five levels it runs, and a call that names a model but no effort runs at that level's effort:

| Level | Model | Effort | What the session-start note names it for |
| :- | :- | :- | :- |
| lookup | Haiku | medium | Finding, reading and listing |
| standard | Sonnet | medium | Everyday work to a clear brief |
| complex | Opus | high | Work that is hard to check, high-stakes, ambiguous or broad, such as a bug's cause, a design or a review |
| hardest | Opus | xhigh | Nothing: a call gets here when it names Opus at `xhigh` or `max`, a rule's `level` sets it, or a redo climbs to it |
| frontier | Fable | high | Open-ended research |

A call that names no model runs on its parent's model, as Claude Code decides, with three exceptions. A parent's model over your cap or not usable here (blocked by `availableModels` or `deniedModels`, or routed around by model health) moves to the next stronger usable model, or to the strongest usable one when nothing stronger is left. A rule's `level` raises it to that level's model. A teammate on a parent weaker than Sonnet starts on Sonnet. Claude marks a redo by starting the call's `description` with "Redo:", when a check failed or the result fell short, so a rewritten brief still counts. A redo that names a model or effort asking for more than the run it redoes runs as named; one that names the same or less climbs anyway, so naming the same model again never repeats the run. The climb is from the run it redoes: one effort step up on the same model while that run was below `high`, else the next stronger model at its own level's effort, else one more effort step. A step up that your environment's effort (`CLAUDE_CODE_EFFORT_LEVEL`, `CLAUDE_CODE_SUBAGENT_EFFORT`) or `maxEffortLevel` holds counts as none, so the redo goes to the next model. A workflow stage is never redone, since its script decides what runs again. The trigger is Claude's judgment, and no benchmark run has shown a redo yet.

<a id="who-decides-the-model"></a><a id="the-checks-before-a-turn-ends"></a>Your choices come first. Inside your `max_tier` cap, a model named on the call, a per-agent rule or the agent's own `model` field decides the model, and a rule's `level` can raise it. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` leaves every model as you set it, and `CLAUDE_CODE_SUBAGENT_MODEL` the model of a call that none of those name, even above `max_tier` ([who decides the model](docs/GUIDE.md#who-decides-the-model)). A call Claude marks "Redo:" with no model or effort of its own climbs past a rule's model, an agent's own model and effort, and `CLAUDE_CODE_SUBAGENT_MODEL`, staying inside `max_tier`. It doesn't climb for an agent whose definition the router can't find, one a rule sets to `route: false`, or one whose own model is outside the registry, and under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` it gets only the effort step. The [check before a turn ends](docs/GUIDE.md#the-check-before-a-turn-ends) is in the guide, and the full design in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Benchmarks

Every stored benchmark row measured an older design of the router, one that scored each brief itself. The redesigned plugin, where Claude names each call's model and effort, has not been benchmarked yet, so nothing below says what it saves or costs.

On an Opus 5.5 session the old router solved <!-- bench:opus.solved.router -->70 of 70<!-- /bench --> benchmark runs, as many as Claude Code's defaults, for <!-- bench:opus.pooled.router -->$0.121<!-- /bench --> per solved task against the defaults' <!-- bench:opus.pooled.defaults -->$0.151<!-- /bench -->. It isn't the cheapest setup on these tasks. They are easy enough that every subagent on Haiku at `high` effort also solved <!-- bench:opus.solved.haiku-high -->70 of 70<!-- /bench -->, for <!-- bench:opus.pooled.haiku-high -->$0.075<!-- /bench -->. By task class, none of these runs supports a verdict under the benchmark's current rules: they ran before subagent commands ran in a sandbox, and the router ran in other batches than its baselines.

That run measured the routing code at <!-- bench:opus.commit_code -->`780ee86`<!-- /bench -->, which also ran a different ladder (lookup at `low`, complex on Sonnet at `high`, hardest on Opus at `high`) with the verify gate off. A pilot on six harder tasks, 3 runs per task on an Opus 5.5 session at `c42886d`, also before the redesign, had the router solve 16 of 18 at $0.47 a run, Claude Code's defaults 15 of 18 at $0.26 and every subagent on Haiku 16 of 18 at $0.09. On those tasks the old router cost more than no router. Three runs per cell are too few to call a winner ([raw rows](bench/results/pilot/)). The next rounds add five tasks of work that isn't code and record the model and effort each Agent call named ([how they run](bench/README.md#running-a-round)). [BENCHMARKS.md](docs/BENCHMARKS.md) has every table of the `780ee86` run, the raw data and the commands to rerun it.

## Commands

| Command | What it does |
| :- | :- |
| `/model-router:route <task>` | Explains which model a task would get and why, and how to split it |
| `/model-router:models [reset]` | Lists the models in use, what each runs as, and what is capped, unavailable or routed around. `reset` clears the model health record, so a model routed around is tried again |
| `/model-router:usage [days]` | Shows what ran where over `days` (default 7), with Claude Code's cost estimate per subagent where the plugin's mod runs |

The plugin also ships five role agents for work no better-fitting agent covers: `model-router:scout` and `verifier` on Haiku, `architect` and `reviewer` on Opus at `high`, and `implementer`, which takes the model named on its call ([what each is for](docs/GUIDE.md#role-agents)). Scripts can print the models and usage tables as JSON; the shapes are in the [guide](docs/GUIDE.md#commands).

## Configuration

Three options in `/config` under the plugin, each with a working default: `routing_mode` (`auto`; `off` changes no call and only keeps records), `max_tier` (none) and `verify_gate` (on). What each option does is in the [guide](docs/GUIDE.md#configuration).

Rules and extra models go in `~/.claude/model-router.json` (yours, applies everywhere) or `.claude/model-router.json` (a project's). A project's file only raises: a cloned repository can make its subagents cost more, never run weaker, and it can't get a model past your `max_tier`. The file's shape, every key and the trust rules are in the [guide](docs/GUIDE.md#configuration).

## What it can't do

- It doesn't change the main conversation's model or effort, because a model switch re-reads the conversation uncached. For a hard request, type `ultrathink` or use `/effort`.
- It never picks a model from the work itself: Claude names one, or the agent's definition or a rule does. A call that nothing names a model for stays on its parent's model, except where your cap, what is usable here, a rule's `level` or a teammate's start on Sonnet moves it ([how it works](#how-it-works-picking-a-subagent-model)).
- It leaves forks alone, and an agent whose definition it can't read, such as one defined on the command line, unless the call or your own rule names its model.
- It starts Python on each prompt, agent call, file edit and turn end, about 45 ms each on Python 3.13 and 170 ms on Apple's Python 3.9.6, which compiles the plugin again on every run (median, [performance](docs/PERFORMANCE.md)). With the mod, an agent waits for two of these before it starts, the mod's and the `PreToolUse` hook's, and for a settings read that can take up to 2 seconds and isn't timed.
- It doesn't retry a call that fails on a model error. Set `fallbackModel` (or `--fallback-model`) for that. After a model error the router suggests a usable model to re-issue the call on, except under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, and after two it routes around that model for a day.
- It routes a new model family only once the registry has an entry for it, and does nothing when hooks are switched off.
- Its dollar figures are Claude Code's estimate, not your bill, and on a subscription usage inside the plan isn't billed in dollars. The mod reads them, so there are none without it, and none for workflow agents, which Claude Code doesn't list to a mod. The main conversation's cost is in `/usage`.

[Every limit, and why](docs/GUIDE.md#what-it-cant-do).

## Compatibility

- Other plugins, skills and agents: their agents are routed too. The router never changes which agent runs, and another plugin's hook that denies or asks about a call still applies ([details](docs/GUIDE.md#refused-rewrites)).
- Workflows and ultracode: a model the script names is held to your cap like any call's, and on the session's own model a stage never runs below the session's effort, such as `xhigh` under ultracode ([details](docs/GUIDE.md#workflows-and-ultracode)).
- Permissions: the plugin never approves a tool call. Your permission rules and auto mode still review every spawn.
- Amazon Bedrock, Google Cloud's Agent Platform (Vertex AI) and Microsoft Foundry: routing uses the aliases `haiku`, `sonnet`, `opus` and `fable`, and where one resolves to an older model the mod sends the family's newest ID. Without the mod the `Agent` call takes only an alias, so a model the router picks itself (a rule's raise, a teammate's start, a redo's next model) moves one family up instead, and a model Claude names runs as its alias resolves ([details](docs/GUIDE.md#third-party-providers)). These providers aren't tested live yet.
- Versions: Claude Code 2.1.329 or later. The live runs on 2.1.330 and 2.1.331 on macOS, ultracode included, predate the redesign where Claude names each call's model and effort; the unit tests run on Linux, macOS and Windows in CI. [Release readiness](docs/RELEASE_READINESS.md) lists what is still open.
- Failure: every hook fails open. A bug can cost routing, never block a tool call, a spawn or a session.

<a id="security"></a>
## Privacy and security

Model Router runs on your machine with your user permissions, as every plugin's hooks and mods do ([plugin security](https://code.claude.com/docs/en/plugins/security.md)). On each agent call it reads the agent, model, effort and `description`, and hashes the prompt to know the same call sent again. It reads each prompt you send only for a model it names, the conversation's transcript for the session's model and the commands that ran, and a subagent's transcript for the commands it ran and whether it answered. It never scores a brief, keeps no prompt, brief or description text (ledger rows from 0.1.0 and 0.1.2 can hold descriptions, see [Update and uninstall](#update-and-uninstall)), keeps its state in its own data directory, and sends no prompt or code anywhere. It makes no network call: the check of new Claude Code releases, docs and model retirements runs in this repository's CI, and what it finds reaches you as a plugin update. To see every hook, agent and skill it installs before you install it, run `claude --plugin-dir ./plugins/model-router plugin details model-router` from a clone. [SECURITY.md](docs/SECURITY.md) has the threat model, what is stored and how to report a problem (there is no private channel yet, so never put details in a public issue).

## Update and uninstall

- Update: run `claude plugin update model-router@claude-code-model-router`, or choose **Update now** on the **Installed** tab of `/plugin`. Auto-update is off by default for third-party marketplaces like this one. To turn it on, run `/plugin`, open the **Marketplaces** tab, select `claude-code-model-router` and turn on auto-update ([Claude Code docs](https://code.claude.com/docs/en/plugins/install.md)). Registry and model fixes ship as plugin updates, so `claude plugin update` gets one now.
- Coming from 0.1.0 or 0.1.2: those releases picked a level from each brief, and now Claude names the model, so a call that names none runs on its parent's model. The options `priority`, `agent_pins`, `main_effort`, `update_check` and `strong_agent_gate` are gone, and a saved `routing_mode` of `advise` now reads as `off`: set `auto` in `/config` to route. The plugin no longer reads `learned.json`, `upstream.json`, `mod.json` or `prompts.json` in its data folder, so you can delete them. Ledger rows those releases wrote keep up to 120 characters of each call's description until they age out after 30 days; delete `ledger.jsonl` there to drop them now. The [changelog](CHANGELOG.md) has the full list.
- Pause it: set `routing_mode` to `off` in `/config`.
- Remove it: `claude plugin uninstall model-router@claude-code-model-router`, or `/plugin uninstall model-router` in a session. Removing the last install also deletes the plugin's data directory, with its ledger and model health record, unless you pass `--keep-data`. Your own `model-router.json` files stay.

## Documentation

[docs/FAQ.md](docs/FAQ.md) answers the questions people ask first, each in its first sentence.

| Page | Answers |
| :- | :- |
| [Guide](docs/GUIDE.md) | How do I configure it for my setup? |
| [Architecture](docs/ARCHITECTURE.md) | How do two paths share one router? |
| [Built on the docs](docs/CLAUDE_CODE_DOCS.md) | Which Claude Code docs does it follow, and where? |
| [Benchmarks](docs/BENCHMARKS.md) | What does a solved task cost, with and without it? |
| [Security](docs/SECURITY.md) | What reaches the network, and who can steer it? |
| [Changelog](CHANGELOG.md) | What changed in each release? |

The [documentation index](docs/README.md) lists every other page, including performance, the roadmap, the glossary and release readiness. Bugs and questions go to [GitHub issues](https://github.com/crisnahine/claude-code-model-router/issues). For a quick question, or a call it routed wrong, join the [Discord server](https://discord.gg/AHZZ7bABJy). To work on the plugin, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).

Copyright 2026 Cris Nahine.

The music in the [video](docs/assets/BRAND.md) is by Sascha Ende ([ende.app](https://ende.app/en)) under CC BY 4.0, so the Apache-2.0 license doesn't cover it.

Model Router is an independent project, not made or endorsed by Anthropic.
