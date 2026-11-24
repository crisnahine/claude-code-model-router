<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Next: <a href="GUIDE.md">Guide</a></p>
<!-- End of page head. -->

# Model Router FAQ: questions about the Claude Code model router

Short answers about Model Router, the Claude Code plugin with the id `model-router`, each with a link to the page it comes from.

## What is Model Router?

Model Router is an open-source Claude Code plugin that keeps the Claude model and reasoning effort of every agent Claude Code starts inside your limits, sets the effort nobody named, and runs a call Claude marks as a redo with more effort or on a stronger model. It is an independent project by Cris Nahine, not made or endorsed by Anthropic. It covers subagents from any plugin, skill or agent, agent-team teammates and workflow stages, and leaves the agent, its prompt and its tools as they were. It is Apache-2.0 licensed and needs Claude Code 2.1.329 or later and Python 3.9 or later ([README](../README.md#install-the-claude-code-plugin)).

## Does Model Router follow the Claude Code docs?

Yes. It uses only extension points Claude Code documents (the plugin manifest, hooks, the mods API, skills and subagents), and follows the docs' rules on subagent models, effort, the prompt cache and failing hooks. [Built on the docs](CLAUDE_CODE_DOCS.md) lists each rule with its doc page and test, and where the plugin goes past the docs.

## How does Model Router pick a model for a subagent?

It doesn't pick from the work: Claude, which has read the work, names the model on each call, and the router keeps it inside your cap and the models that work here, raises it to a rule's level, and sets the effort nobody named. The model comes from, first found: `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` for a workflow agent, a model named on the call, a per-agent rule, the agent's own `model` field, `CLAUDE_CODE_SUBAGENT_MODEL`, and then the parent's model. Each model stands for a level, and a call that names no effort runs at that level's: Haiku at medium (lookup), Sonnet at medium (standard), Opus at high (complex), Opus at xhigh (hardest, when the call names `xhigh` or `max`) and Fable at high (frontier) ([ARCHITECTURE.md](ARCHITECTURE.md#the-decision)).

## What happens when the cheap model gets it wrong?

A call Claude marks as a redo, by starting its `description` with "Redo:", climbs from the run it redoes. Claude marks one when a check failed or the result fell short, so the trigger is its judgment. A redo that names its own model or effort runs as named. Otherwise a run below `high` effort is redone one effort step up on the same model, and one at `high` or above on the next stronger usable model at that model's own level's effort: Sonnet at medium redoes on Sonnet at high, then on Opus at high. With no stronger model it takes one more effort step, up to `max`, or `high` on Haiku. The same call sent again unmarked is a new call, since a check run again after a fix is no redo. A redo never changes later calls. `/model-router:usage` counts redos by level, and a level that keeps coming back can be raised in your own `~/.claude/model-router.json` ([ARCHITECTURE.md](ARCHITECTURE.md#redo-escalation)).

## What does Model Router cost?

The plugin is free, and it adds 683 tokens to the main conversation's context (subagents get none), measured on a headless Sonnet run. Each hook takes about 54 to 60 ms warm, and a `mod-spawn` round trip takes about 58 ms warm and 187 ms cold (p50), almost all of it starting Python ([PERFORMANCE.md](PERFORMANCE.md)). The models it routes to are billed to your Claude plan or API account as usual, and `/model-router:usage` shows Claude Code's own cost estimate for each subagent, which isn't the bill.

## Does Model Router reduce Claude Code cost?

Not measured yet for today's defaults: the last benchmark ran the routing code at commit <!-- bench:opus.commit_code -->`780ee86`<!-- /bench -->, before complex moved to Opus and the verify gate was turned on. On that run's Opus 5.5 session the router cost <!-- bench:opus.pooled.router -->$0.121<!-- /bench --> per solved task against <!-- bench:opus.pooled.defaults -->$0.151<!-- /bench --> for Claude Code's defaults over <!-- bench:opus.runs.defaults -->70<!-- /bench --> runs each (<!-- bench:opus.pooled_less.defaults -->20<!-- /bench -->% less), with every run solved for both. By class, none of these runs supports a verdict under the benchmark's current rules: they ran before subagent commands ran in a sandbox, and the router ran in other batches than its baselines. It isn't the cheapest setup there: every subagent on Haiku at `high` effort (<!-- bench:opus.pooled.haiku-high -->$0.075<!-- /bench -->, <!-- bench:opus.solved.haiku-high -->70 of 70<!-- /bench --> solved) and `sonnet-via-opusplan`, which runs Sonnet throughout a headless run (<!-- bench:opus.pooled.sonnet-via-opusplan -->$0.069<!-- /bench -->, <!-- bench:opus.solved.sonnet-via-opusplan -->69 of 70<!-- /bench -->), cost less in every class. Each class and configuration ran <!-- bench:per_cell -->5 or 10<!-- /bench --> times ([BENCHMARKS.md](BENCHMARKS.md), [TRACEABILITY.md](TRACEABILITY.md) row S4.15).

## Does quality drop when subagents run on cheaper models?

The benchmark can't tell yet. In the run of the routing code at commit <!-- bench:opus.commit_code -->`780ee86`<!-- /bench -->, the router solved as many tasks as Claude Code's defaults, <!-- bench:opus.solved.router -->70 of 70<!-- /bench --> for each on an Opus 5.5 session, and <!-- bench:sonnet.solved.router -->69 of 70<!-- /bench --> against <!-- bench:sonnet.solved.defaults -->68 of 70<!-- /bench --> on a Sonnet session, <!-- bench:per_cell -->5 or 10<!-- /bench --> runs per class and configuration. That says little about quality. <!-- bench:opus.saturated -->5<!-- /bench --> of <!-- bench:opus.classes -->8<!-- /bench --> Opus classes are saturated: every subagent on Haiku solved as many runs there as every subagent on Fable at `high`, within noise, so those tasks can't tell models apart. On a Sonnet session no baseline forces a second model, so no class gets a quality verdict. The router's one Sonnet miss was a design task, where every subagent on Sonnet at medium solved <!-- bench:sonnet.cell_solved.design.sonnet-medium -->10 of 10<!-- /bench -->. Two things guard quality: a call Claude marks as a redo runs with more effort or on a stronger model, and the `verify_gate` check asks for the work's own check when project files changed and nothing checked them ([BENCHMARKS.md](BENCHMARKS.md), [guide](GUIDE.md#the-check-before-a-turn-ends)).

## How do I turn Model Router off, or turn parts of it off?

Set the plugin's `routing_mode` option to `off` in `/config`, and the router changes nothing and only keeps its ledger. `verify_gate` (on) switches the check before a turn ends. To leave one agent alone, set `"route": false` for it under `agents` in `~/.claude/model-router.json`; a project's file can't ([README](../README.md#configuration)).

## Can I cap the strongest model, or force one model?

Yes: `max_tier` caps the strongest model subagents may run on, and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` pins every subagent to one model. With the cap at `sonnet`, any model above Sonnet is lowered to it, whether a caller, a rule, the agent or the parent's model named it. A model the cap leaves weaker than its level needs gets one more effort step, up to `xhigh`, or `high` on Haiku. A call left on the parent's model runs at the level of the model the cap leaves. With the force variable set, the router only sets effort, and a pinned strong model costs far more on small jobs: Opus 5.5's list price is 40 times Haiku 5.5's per token ([guide](GUIDE.md#who-decides-the-model)).

## How is this different from setting one model for every agent?

`CLAUDE_CODE_SUBAGENT_MODEL` gives every agent without its own model the same model, while with Model Router Claude names the model each call needs. One model for everything puts lookups on an expensive model or hard design work on a weak one. While the variable is set, a call that names no model runs on it and the router leaves it alone, unless Claude marks it as a redo ([ARCHITECTURE.md](ARCHITECTURE.md#the-decision)).

## Does it change the main conversation's model?

No. Each model keeps its own prompt cache, so a switch would re-read the whole conversation uncached. It leaves the main conversation's effort alone too: for a hard request, type `ultrathink` or use `/effort` ([README](../README.md#what-it-cant-do)).

## Does Model Router work on Amazon Bedrock, Google Vertex AI and Microsoft Foundry?

Yes, through the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves for your provider, though these providers aren't tested live yet. Where an alias resolves to an older model, the mod starts routed work on the family's newest model (`claude-opus-5-5`, `claude-sonnet-5-5` or `claude-haiku-5-5`) and falls back to the alias if the provider refuses it. `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` keeps the alias ([guide](GUIDE.md#third-party-providers)).

## What does Model Router store on my machine, and what leaves it?

It keeps its state in its own plugin data directory and sends no prompt, brief, path or code anywhere. The ledger keeps levels, reasons, model names, token counts and Claude Code's cost estimate per subagent, never a call's brief or description, for 30 days; a call and its task are recognized by a hash. Its folder is readable by your user alone. It makes no network call: new Claude Code releases, docs changes and model retirements are checked in this repository's CI, and fixes reach you as plugin updates ([SECURITY.md](SECURITY.md#network)).

## Does it work with other plugins, skills and agents?

Yes. It routes agents from any plugin, skill or agent, and never changes which agent runs. Another plugin's hook that denies or asks about a call still applies. An agent whose definition names its own model keeps it, inside your cap ([README](../README.md#compatibility)).

## Does Model Router work with workflows and ultracode?

Yes. A model the script names is held to your cap like a subagent's, and on the session's own model a stage never runs below the session's effort, such as `xhigh` under ultracode. The live run under `claude --effort ultracode` on Claude Code 2.1.331 predates the caller naming each model, so it doesn't test this behavior ([guide](GUIDE.md#workflows-and-ultracode)).

## Does it approve tool calls or get around my permissions?

No. The plugin never approves a tool call, and your permission rules and auto mode review every spawn as they would without it. Its one refusal is on the fallback path: a call it already rewrote that came back without running, when the call's own model is above your cap. The refusal names the model to use instead ([SECURITY.md](SECURITY.md#what-the-plugin-does)).

## How do I see what it routed and what it spent?

Run `/model-router:usage` for what ran where and Claude Code's cost estimate per subagent, and `/model-router:models` for the models in use, what each runs as, and what is capped, unavailable or routed around. `/model-router:route <task>` explains which model a call would run on and why before you run it ([README](../README.md#commands)).
