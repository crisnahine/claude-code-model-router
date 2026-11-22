<!-- Page head, written by tools/docs_assets.py: edit its PAGES table, then run it. -->
<p align="center"><img src="assets/banner-faq.svg" width="880" alt="Questions: What is Model Router, and how does it pick?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Next: <a href="README.md">Documentation</a></p>
<!-- End of page head. -->

# Model Router FAQ: questions about the Claude Code model router

Short answers about Model Router, the Claude Code plugin with the id `model-router`, each with a link to the page it comes from.

## What is Model Router?

Model Router is an open-source Claude Code plugin that sets the Claude model and reasoning effort of every agent Claude Code starts, and moves a call one level up when its work comes back for a redo. It covers subagents from any plugin, skill or agent, agent-team teammates and workflow stages. The agent the caller chose, its prompt and its tools stay as they were. It is Apache-2.0 licensed and needs Claude Code 2.1.292 or later and Python 3.9 or later ([README](../README.md#install-the-claude-code-plugin)).

## How does Model Router pick a model for a subagent?

Model Router reads the subagent's brief and the agent's name, gives the call one of five levels, and runs it on the cheapest usable model and effort for that level. The levels are lookup (Haiku at low effort), standard (Sonnet at medium), complex (Sonnet at high), hardest (Opus at high) and frontier (Fable at high). The brief is scored for risk, scope, load and ambiguity, and for whether the call changes something or only reads. A model you or the caller named still wins: the order is `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, a model named on the call, a per-agent rule, the agent's own `model` field, `CLAUDE_CODE_SUBAGENT_MODEL`, and only then the router's pick ([ARCHITECTURE.md](ARCHITECTURE.md#the-decision)).

## What happens when the cheap model gets it wrong?

When Claude issues the same call again in the same request after its result came back, that call runs one level higher. Lookup, complex and hardest move to a stronger model on a redo, standard moves to complex and stays on Sonnet at more effort, and a frontier redo runs at one more step of effort. Each redo is also a vote that the first level was too low: at least 6 votes that are also at least 30% of a level's calls raise that level for later calls ([ARCHITECTURE.md](ARCHITECTURE.md#redo-escalation)).

## What does Model Router cost?

The plugin is free under the Apache-2.0 license, and it adds 683 tokens to each request's context, measured on a headless Sonnet run. Each of its hooks takes about 54 to 60 ms warm on the measuring machine, and a spawn decision settled in about 110 ms in live runs, starting Python included ([PERFORMANCE.md](PERFORMANCE.md)). The models it routes to are billed by your Claude plan or API account as usual. `/model-router:usage` shows what each turn cost at list price, main conversation and subagents apart.

## Does Model Router reduce Claude Code cost?

On an Opus 5.5 session, yes: the router cost less per solved task than Claude Code's defaults in all 8 task classes, $0.122 against $0.152 over 80 runs each (19% less), with every run solved for both. The difference is outside the noise in 6 classes (17 to 35% less) and inside it in design and research. On a Sonnet session, no: the router is cheapest only in lookup and costs 9 to 72% more per solved task than the best baseline in the other 7 classes, at the same pass rate. Both ran the shipped 0.1.1, 10 runs per class and configuration ([BENCHMARKS.md](BENCHMARKS.md), [TRACEABILITY.md](TRACEABILITY.md) row S4.15).

## Does quality drop when subagents run on cheaper models?

In the 0.1.1 benchmark the router solved as many tasks as Claude Code's defaults: 80 of 80 for each on an Opus 5.5 session, and 79 of 80 against 78 of 80 on a Sonnet session, 10 runs per class and configuration. Its one Sonnet miss was a design task, where the best baseline, every subagent on Sonnet at medium, solved 10 of 10. Two things guard quality: a redo runs one level higher, and the `strong_agent_gate` check, on by default, asks once for a stronger agent's review when a hard request was answered by a weaker session model alone. The profile is an estimate from the brief's wording, so Claude can overrule it on any call ([BENCHMARKS.md](BENCHMARKS.md), [README](../README.md#the-checks-before-a-turn-ends)).

## How do I turn Model Router off, or turn parts of it off?

Set the plugin's `routing_mode` option to `off` in `/config`, and the router changes nothing and only keeps its ledger. `advise` tells Claude the estimate without changing any call. The other parts each have their own option: `strong_agent_gate` (on) and `verify_gate` (off) for the checks before a turn ends, `main_effort` (off) for the main conversation's effort, and `update_check` (on) for the daily fetch. To leave one agent alone, give it `"route": false` under `agents` in `~/.claude/model-router.json` or the project's `.claude/model-router.json` ([README](../README.md#configuration)).

## Can I cap the strongest model, or force one model?

Yes: `max_tier` caps the strongest model subagents may run on, and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` pins every subagent to one model. With the cap set to `sonnet`, for example, anything the router or a caller picks above Sonnet is lowered to it. With the force variable set, the router only sets effort. A pinned strong model costs far more on small jobs: in a live test, listing a directory on a forced Opus subagent cost about 30 times what the same subagent cost on Haiku ($0.39 against $0.012) ([README](../README.md#who-decides-the-model)).

## How is this different from setting one model for every agent?

Setting `CLAUDE_CODE_SUBAGENT_MODEL` gives every agent without its own model the same model, while Model Router gives each call the model and effort its brief needs. One model for everything either puts lookups on a strong, expensive model or puts hard design work on a weak one. The variable also outranks the router's pick: while it is set, agents that name no model run on it and the router leaves them alone, unless `agent_pins` is `override`. Leave it unset to let the router choose ([ARCHITECTURE.md](ARCHITECTURE.md#the-decision)).

## Does it change the main conversation's model?

No, Model Router never switches the main conversation's model, because each model keeps its own prompt cache and a switch would re-read the whole conversation uncached. With the `main_effort` option on (it is off by default), it raises the main conversation's effort for one turn when a request looks complex or harder, and it never lowers what you set. When a request looks harder than the session's model, Claude is told to hand the hard thinking to a strong subagent ([README](../README.md#what-it-cant-do)).

## Does Model Router work on Amazon Bedrock, Google Vertex AI and Microsoft Foundry?

Yes, routing works on third-party providers through the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves for your provider. Some providers resolve an alias to an older model, so on those the mod starts routed work on the family's newest model (`claude-opus-5-5`, `claude-sonnet-5-5` or `claude-haiku-5-5`) and falls back to the alias if the provider refuses it. Setting `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` keeps the alias. The main conversation's effort is left alone on Bedrock, Vertex and Foundry, where an effort change would re-read the context uncached ([README](../README.md#compatibility)).

## What does Model Router store on my machine, and what leaves it?

Model Router keeps its state in its own plugin data directory and sends no prompt, brief, path or code anywhere. The ledger keeps the first 120 characters of each agent call's description, plus levels, reasons, model names, token counts and list-price cost; briefs and prompts themselves are not stored, and a repeated call is recognized by a hash. The only network traffic is the daily update check: the Claude Code changelog, docs index and model pages from `code.claude.com` and `platform.claude.com`, and your account's model list from `api.anthropic.com` when an Anthropic API key is set. Turning `update_check` off stops that fetch ([SECURITY.md](SECURITY.md)).

## Does it work with other plugins, skills and agents?

Yes, Model Router routes agents from any plugin, skill or agent, and it never changes which agent runs or refuses a call to steer it. Another plugin's hook that denies or asks about a call still applies. An agent whose definition names its own model keeps it unless you set `agent_pins` to `override` ([README](../README.md#compatibility)).

## Does it approve tool calls or get around my permissions?

No, the plugin never approves a tool call, and your permission rules and auto mode review every spawn as they would without it. The one thing it refuses is on the fallback path: a call it already rewrote that came back without running, when the call's own model is above your cap. The refusal names the model to use instead ([SECURITY.md](SECURITY.md#what-the-plugin-does)).

## How do I see what it routed and what it spent?

Run `/model-router:usage` for what ran where and what it cost at list price, and `/model-router:models` for the models in use, what each runs as, and what is capped, unavailable or learned. `/model-router:route <task>` explains which model a task would get and why before you run it. Both `usage` and `models` take `--json` for scripts ([README](../README.md#commands)).
