<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="GUIDE.md">Guide</a> &nbsp;&middot;&nbsp; Next: <a href="ARCHITECTURE.md">Architecture</a></p>
<!-- End of page head. -->

# Model Router FAQ: questions about the Claude Code model router

Short answers about Model Router 0.2.0, the Claude Code plugin with the id `model-router`.

## What is Model Router?

Model Router is an open-source Claude Code plugin that keeps the model and effort Claude names for each subagent, teammate and workflow agent within your limits. It fills in a missing effort, gives a call that names no model a level from what its agent can do, raises a call to a rule's level, routes around a failing model, and runs a call Claude marks as a redo one step higher. It is an independent Apache-2.0 project by Cris Nahine, not made or endorsed by Anthropic, and needs Claude Code 2.1.329 or later and Python 3.9 or later.

## Does it pick the model, or does Claude?

Claude does, from a table the plugin gives it at session start: Haiku at `medium` for finding, reading and listing, Haiku at `high` for writing and fixing code, figures and documents to a brief, and Sonnet at `high` for a judgment call with no check to run, such as a design or a review. Opus and Fable have no row; a subagent runs on one when a redo climbs there, a rule sets its level or the call names it. A call that names no model runs on its agent's own model, else `CLAUDE_CODE_SUBAGENT_MODEL`, else at the level its tools put it, and the plugin never reads the brief ([guide](GUIDE.md#who-decides-the-model)).

## What happens when a subagent gets it wrong?

Claude marks a redo by starting the call's `description` with "Redo:". The redo runs above the run it redoes: one effort step up while that run was below `high`, else on the next stronger model, inside your cap. A workflow stage is never redone ([guide](GUIDE.md#redo)).

## Does it cost anything?

The plugin is free and makes no model call of its own; the models it runs bill to your plan or API account as usual. The check before a turn ends costs one more turn when it asks. Each hook starts Python, about 50 ms on Python 3.13 and 80 to 100 ms on Apple's Python 3.9.6 ([PERFORMANCE.md](PERFORMANCE.md)), and [BENCHMARKS.md](BENCHMARKS.md) holds the cost and quality results.

## What does it add to the context?

A session-start note of about 630 characters holding the table above, which the mod also adds to the Agent tool's description, with a form of about 470 characters in the Workflow tool's, plus the descriptions of its `route` skill and five agents. Claude also gets one line when your prompt names a model, when a check is due before a turn ends, after a model error, a mid-run model swap or a redo with nothing stronger left, and, without the mod, on each call it rewrites. It adds nothing to a subagent's brief, and `claude plugin details model-router` shows what it adds to every session.

## Does anything leave my machine?

No. It makes no network call and sends no prompt, brief or code anywhere. Its state stays in its own data folder, and its ledger keeps model names, levels, tokens and cost estimates, never a prompt or description ([SECURITY.md](SECURITY.md)).

## Which models does it use?

The aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves for your provider, or, where the mod runs and your provider's alias runs an older model, the family's newest full ID. `max_tier` caps the strongest one, though not a model `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` or `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` pins. `/model-router:models` shows which are usable here and which are capped, blocked or routed around ([guide](GUIDE.md#configuration)).

## What happens without Python, or on an older Claude Code?

Without Python 3.9 or later on `PATH`, its hooks do nothing and its commands print one line saying why. Claude Code still runs the role agents on the models their definitions name, and the mod still lowers a model a call names above `max_tier`. Claude Code 2.1.323 to 2.1.328 refuse to load the plugin, 2.1.271 to 2.1.322 don't check the version it needs, and older versions can't load it, so update Claude Code first.

## How do I turn it off?

Set `routing_mode` to `off` in `/config`: it changes no call and adds no session note or check, but still keeps its records, and `/model-router off` leaves calls as they are for this session only, where the mod runs. Set `verify_gate` to `false` to drop only the check before a turn ends, or give one agent `"route": false` in your own `~/.claude/model-router.json`. To unload it, run `claude plugin disable model-router@claude-code-model-router` ([guide](GUIDE.md#configuration)).

## How do I see what it did?

Where the mod runs, a line under the prompt shows the mode, the cap, this session's calls and how many the router changed or ran as redos, and `/model-router` prints it with no model turn. `/model-router:usage` shows the calls per agent, how many the router changed, their levels, the models they ran on and, where the mod runs, Claude Code's cost estimate, and `/model-router:models` shows each model's state. `/model-router:route <task>` says what a call would run on and why ([guide](GUIDE.md#commands)).

## Does it change the main conversation's model?

Only when you pick a setup. When a new session starts below the setups Model Router recommends for the session that plans and delegates (Opus or Fable at `medium` effort or higher, or Sonnet at `xhigh` or higher), the mod asks once, before your first prompt, and a pick runs Claude Code's `/model` and `/effort`, which also make it your default for new sessions. It never switches by itself, since a switch re-reads the whole conversation uncached. `Don't ask again`, `/model-router check off` or `session_check` set to `off` in `/config` stops the question ([guide](GUIDE.md#the-session-check)).
