# Claude Code Orchestrator: autonomous multi-model orchestration plugin for Claude Code

**Orchestrator** is a Claude Code plugin for autonomous multi-model orchestration. It routes each piece of work to the Claude model that fits it (Haiku, Sonnet, Opus or Fable) by complexity, risk and cost, runs a plan, build, verify and review pipeline with as little hand-holding as is safe, enforces a cost cap on model tiers, and tells you when a Claude Code release or docs change affects your setup.

- **Model routing by task complexity.** Seven specialized subagents, each pinned to the cheapest model that does its job well, plus a routing rubric Claude applies before delegating.
- **Autonomous development lifecycle.** `/orchestrator:ship` takes a task or GitHub issue through planning, implementation, verification and adversarial review, with fix loops, and stops before anything irreversible.
- **Cost control.** A tier cap refuses subagent spawns above the model you allow, and a ledger shows which agents ran on which models.
- **Stays current with Claude Code.** A daily check of the official changelog and docs index flags new releases and changes to the plugin, hook, skill, subagent and model docs, and `/orchestrator:refresh` turns them into an impact report.

## Install

In Claude Code:

```text
/plugin marketplace add crisnahine/claude-code-orchestrator
/plugin install orchestrator@claude-code-orchestrator
```

Requirements: Claude Code 2.1.270 or later, `sh`, and Python 3.9+ as `python3` or `python` on your `PATH`. Without Python the plugin stays out of the way: hooks do nothing and skills say why. The update check needs network access to `code.claude.com`.

To try it from a clone without installing: `claude --plugin-dir ./plugins/orchestrator`.

## How routing works

| Agent | Model | Effort | Use it for |
| :- | :- | :- | :- |
| `orchestrator:scout` | Haiku | low | Read-only search: where is X, what does Y do |
| `orchestrator:verifier` | Haiku | low | Run tests, lint, type checks and report |
| `orchestrator:docs-researcher` | Haiku | medium | Read the live Claude Code docs and changelog |
| `orchestrator:implementer` | Sonnet | medium | Write code and tests to a clear plan |
| `orchestrator:architect` | Opus | high | Plans, system design, subtle bugs, risky changes |
| `orchestrator:reviewer` | Opus | high | Adversarial review of every non-trivial diff |
| `orchestrator:deep-solver` | Fable | xhigh | The hardest, longest, most ambiguous work |

The `orchestrator:route` skill scores a task on ambiguity, scope, risk, novelty and how hard it is to verify, then picks the cheapest tier that can do it reliably, splits independent work across parallel agents, and escalates one tier when a result fails verification. Claude uses it on its own for non-trivial tasks; you can also ask "which model should do this?"

Model choice is enforced by Claude Code itself through each agent's `model` frontmatter, so routing doesn't depend on Claude remembering a rule.

## Automatic by default

Once installed, the orchestrator is used without anyone asking for it, whatever else is installed:

1. **Every session and every prompt** gets the routing policy as context, so it applies inside your own skills and other plugins' skills too, not only when an orchestrator skill is running.
2. **Generic built-in agent calls are redirected.** When Claude, or a skill from any plugin, tries to start `Explore`, `Plan` or `general-purpose`, a hook refuses the call and names the orchestrator agent to use instead (`Explore` to `scout`, `Plan` to `architect`, `general-purpose` to whichever of ours fits). Claude re-issues the call with that agent.
3. **You stay in charge.** If you name an agent ("use the Explore agent", "a general-purpose agent"), you get it for the rest of the session. Your own agents and other plugins' agents are never redirected or blocked, including your own agent named `Explore` if you replaced the built-in. Your skills still run; only the delegation inside them is routed.

The **Routing mode** option in `/config` sets how hard this pushes:

| Mode | Policy as context | Generic agent calls |
| :- | :- | :- |
| `steer` (default) | Yes | Redirected once per prompt, including every call of a parallel batch. If Claude then issues the same call again, it is allowed, so nothing can get stuck |
| `strict` | Yes | Always refused unless you named the agent in your prompt |
| `guide` | Yes | Not touched |
| `off` | No | Not touched. The tier cap and the usage ledger still work |

A repository can turn routing down for everyone who works in it by setting `"routing_mode"` in `.claude/orchestrator.json`, for example `{"routing_mode": "off"}`. The weaker of your setting and the repository's wins, so a repository can opt out but can't force `strict` on you.

What this guarantees and what it doesn't:

- **Guaranteed:** a redirected call does not run; an orchestrator agent runs on its pinned model; in `strict`, the built-in agents never run unless you name them.
- **Best effort:** which of our agents Claude picks, and whether Claude delegates at all. Work the main conversation does itself runs on your session's model; a plugin can't change that model.
- **Out of reach:** agents started by workflows or agent teams, other plugins' own agents, forks of the conversation, and a skill that runs itself in a subagent with `context: fork` (Claude Code starts that one directly, without an agent call a hook can see).
- **Settings that switch it off:** `disableAllHooks`, and `allowManagedHooksOnly` in an organization's managed settings unless the organization force-enables this plugin. The agents and skills still work then; the automatic redirect, the cap and the ledger don't.
- **Other plugins' hooks:** if another plugin's hook allows an agent call that ours refuses, the refusal wins, because Claude Code ranks deny above allow. If another plugin's hook refuses one of our agents, that refusal wins too.
- **Cost:** the per-prompt reminder adds about 60 tokens to each prompt.

## Commands

| Command | What it does |
| :- | :- |
| `/orchestrator:ship <task or issue>` | Understand, plan (Opus), build (Sonnet, in parallel where possible), verify (Haiku), review (Opus), fix up to 2 loops, commit on a branch. Never pushes, opens a PR, merges or deploys unless you asked in the same request |
| `/orchestrator:route <task>` | Recommend an agent and model for a task, with the reasons |
| `/orchestrator:refresh` | Fetch the Claude Code changelog and docs index, list what changed since your last review, and have the docs researcher explain the impact |
| `/orchestrator:usage [days]` | Which agents ran, on which models, and how often the cap refused a spawn |

## Configuration

Run `/config` and find the Orchestrator rows:

- **Routing mode** (`routing_mode`): `steer` (default), `strict`, `guide` or `off`. See [Automatic by default](#automatic-by-default).
- **Highest model tier** (`max_tier`): `haiku`, `sonnet`, `opus` or `fable` (default, no cap). A subagent spawn above it is refused, and Claude is told which cheaper agent or model to use instead. This includes built-in agents that inherit your session's model, so with the cap at `sonnet` and an Opus session, Explore runs are retried on Sonnet.
- **Check for Claude Code updates** (`update_check`): on by default. Turns off the daily fetch from `code.claude.com`.

To extend routing for one project, add `.claude/orchestrator.json` with the parts of [`config/routing.json`](plugins/orchestrator/config/routing.json) you want to merge in. The most useful case is registering your project's own subagents, so the routing table lists them and the tier cap knows their models:

```json
{ "agents": { "db-migrator": { "model": "opus", "role": "Writes and checks schema migrations" } } }
```

This file describes models; it doesn't change them. An agent's real model always comes from its own `model` frontmatter.

## What "autonomous" and "self-updating" mean here

Being specific about limits, so you know what you're getting:

- **Routing is real, but advisory where Claude chooses.** Agent models are enforced by Claude Code. Which agent Claude picks for a task follows the routing skill, which is guidance Claude follows well but not a hard rule. The tier cap is the hard rule.
- **Staying current means detection plus review, not silent self-modification.** The plugin never rewrites itself on your machine. It detects upstream changes daily, and in this repository a scheduled GitHub Action opens an issue when a release or a relevant docs page changes. Updates ship as reviewed releases of the plugin, which you get through `/plugin` updates.
- **Minimal human intervention, not zero.** `/orchestrator:ship` runs end to end without questions, except when a plan changes a public API, schema, security behavior or dependencies, and it never pushes, merges or deploys without being asked. Those are deliberate stops.
- **Cost reporting is about routing.** Token and dollar totals come from Claude Code's own `/usage` and `/cost`; the ledger records which agents and models ran.

## Compatibility and edge cases

What has been checked, and how:

| Situation | Status |
| :- | :- |
| Claude Code 2.1.270, 2.1.300 and 2.1.330 | Loaded and run live: agents spawn on their models and hooks fire |
| Another plugin with an agent named `reviewer`, a skill named `route` and its own `Agent` hooks | Run live: both plugins' hooks run, names stay separate, the cap only judges agents whose model it knows |
| Your own `.claude/agents/scout.md` or `~/.claude/agents/reviewer.md` | Unit tested and run live: a bare `scout` is treated as yours, `orchestrator:scout` as ours |
| Plugin installed under a path with spaces | Run live (skills and hooks) |
| Built-in agents (Explore, Plan, general-purpose) that inherit the session model | Run live: the cap uses the session's model, read from the transcript. Following `/model` switches is unit tested |
| `CLAUDE_CODE_SUBAGENT_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` | Unit tested, following the documented precedence |
| Offline, corrupt state files, malformed `.claude/orchestrator.json`, no Python | Unit tested: hooks exit cleanly and never block a session |
| Another plugin's skill that insists on `Explore`, and a project skill that asks for "a subagent" | Run live: the call was redirected and ran on `orchestrator:scout` (Haiku) |
| You name a built-in agent in your prompt | Run live: it runs, not redirected |
| Another plugin whose hook returns "allow" for every agent call and whose prompt hook says "always use Explore" | Run live: our redirect still wins |
| Three `Explore` calls issued in parallel | Run live: all three redirected, then run as three scouts |
| Claude repeats the same refused call (`steer`) | Run live: allowed the second time. In `strict`, set through `/config`, refused both times |
| Your project defines its own agent named `Explore` | Run live: your agent runs, not redirected |
| A permission rule denies `orchestrator:scout` | Run live: Claude fell back to the built-in agent on the repeat call |
| `disableAllHooks` | Run live: no redirect, as documented; agents and skills still load |
| Options set through `/config` (stored in `pluginConfigs`) | Run live: `routing_mode` and `max_tier` reach the hooks |
| A skill with `context: fork` and `agent: Explore` | Run live: not routed. Claude Code starts it without an agent call |
| Background-task notifications between turns | Unit tested, from real captured hook input: they don't reset what you asked for |
| Linux, macOS and Windows, Python 3.9 to 3.13 | Hook logic unit tested in CI on all three systems with Python 3.10 and 3.13, and locally on 3.9 |
| Fable | Run live: `deep-solver` ran on `claude-fable-5-2` |

Known limits:

- **The cap only sees the `Agent` tool.** Agents started by a [workflow](https://code.claude.com/docs/en/workflows), agent-team teammates, and a mod that sets the model in `agent.spawn` aren't checked.
- **Inherited models are detected best-effort.** On the very first turn of a session, before Claude has replied once, an inheriting built-in agent can't be checked and is allowed.
- **Models your plan or gateway doesn't serve.** Claude Code falls back as described in its [subagent docs](https://code.claude.com/docs/en/sub-agents#when-an-allowlist-blocks-a-subagent-model). If your gateway doesn't serve Fable, set the cap to `opus` so Claude routes the hardest work there directly.
- **Windows.** Hooks need `sh`, which Git for Windows provides. Claude Code on Windows without Git Bash hasn't been tested.
- **Another plugin's agents** are never capped, since their models aren't knowable from the outside.

## How it's built

Only documented Claude Code plugin mechanisms are used: subagents with `model` and `effort` frontmatter, skills with dynamic context injection, `SessionStart`, `PreToolUse` and `PostToolUse` hooks on the `Agent` tool, `userConfig` options, and a plugin marketplace. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```text
.claude-plugin/marketplace.json   marketplace that lists the plugin
plugins/orchestrator/
  .claude-plugin/plugin.json      manifest and user options
  agents/                         seven model-pinned subagents
  skills/                         route, ship, refresh, usage
  hooks/hooks.json                session context, tier cap, ledger, update check
  scripts/orchestrator.sh         launcher that finds Python and never fails a hook
  scripts/orchestrator.py         hook handlers and helpers (Python standard library only)
  config/routing.json             tiers, agents and escalation policy
  evals/                          claude plugin eval cases for routing
compat/baseline.json              last reviewed Claude Code version and docs index
tests/                            unit tests for the hook logic
```

## Development

```bash
python3 -m unittest discover -s tests          # hook logic, config drift checks
claude plugin validate --strict .              # marketplace
claude plugin validate --strict plugins/orchestrator
claude plugin eval plugins/orchestrator        # routing behavior (uses model calls)
```

## License

MIT
