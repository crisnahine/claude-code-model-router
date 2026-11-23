<!-- Page head, written by tools/docs_assets.py: edit its PAGES table, then run it. -->
<p align="center"><img src="docs/assets/banner-glossary.svg" width="880" alt="Glossary: What do call, level, pick and redo mean?"></p>

<p align="center"><a href="README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="docs/ARCHITECTURE.md">Architecture</a> &nbsp;&middot;&nbsp; Next: <a href="docs/research/model-control.md">Research</a></p>
<!-- End of page head. -->

# Glossary

The words this plugin's code, docs and reviews use for its own domain.

**Call**: one request to start an agent: a subagent, a teammate or a workflow stage. A call is known by its agent type and its prompt, so the same call issued twice is recognized.

**Spawn**: the moment a call starts an agent. The mod sees it as the engine's `agent.spawn` event, the fallback as a `PreToolUse` hook on the `Agent` tool.

**Path**: how a call reaches the router. The **mod path** runs in `hooks/router.ts` on engine events. The **fallback path** (`fallback.py`) rewrites the `Agent` tool call and stands down when the mod runs.

**Level**: how much reasoning a call needs: lookup, standard, complex, hardest or frontier. It comes from the call's brief and the agent's name.

**Profile**: the step that reads a brief and an agent's name and gives the level, with the reasons for it (`profile.py`). It looks for signals and for words that mean the call changes something or has to reason, and leaves out what a negated limit such as "do not change any files" says. A brief that fixes or hunts a defect is at least complex.

**Signal**: one kind of difficulty the profile looks for in a brief: risk (security, data and its consistency, migrations, public APIs), scope (several modules or services), load (performance, latency, throughput, staleness and capacity limits, counted only when the brief also asks for a design or a choice) or ambiguity (design, a decision between approaches, root cause, trade-offs). More signals mean a higher level.

**Registry**: the shipped `config/models.json`, merged with the user's and a project's override files: which models exist and their relative strength, speed and cost, the level table, aliases and list prices (`config.py`, `registry.py`).

**Override file**: `~/.claude/model-router.json` or a project's `.claude/model-router.json`, merged over the registry. The user's file is trusted in full. A project's is limited: it can turn routing down but never on, and it can't change the rank of a shipped model or one the user defined.

**Ladder**: the rule that turns a level, the priority and learned feedback into a model and an effort.

**Pick**: the model and effort the ladder gives a call before anyone else's choice is applied.

**Decision**: the model and effort a call finally gets, with the reason (`router.py`). It applies an order of authority (force settings, a model named on the call, agent rules, the agent's pin, the subagent default, then the pick) and lowers the winner to the cap and to what is available.

**Session model**: the model the conversation itself runs on, which subagents inherit when nothing else decides (`session.py`). The mod reads it from the engine. The fallback reads it from the transcript, or from what SessionStart and PostModelSwitch recorded.

**Pin**: a model an agent's own definition names. A pinned agent keeps its model, and gets effort from its level when it names none.

**Cap**: the strongest model the user allows (`max_tier`). Every result is lowered to fit it, never raised.

**Priority**: what the ladder favors (`priority`). `balanced` takes the cheapest model strong enough for the level, `quality` goes one step stronger, and `fast` goes one step lighter and takes the quickest model that qualifies.

**Routing mode**: `auto` sets each call's model and effort, `advise` only tells Claude the estimate, and `off` does nothing (`routing_mode`). When the option and the override files disagree, the most cautious one wins.

**Allowlist** (`allowlist.py`): the `availableModels` and `deniedModels` settings, read the way Claude Code reads them, from the layers the mod sends or from the settings files. Models they block are not picked. When they leave no registry model open, the call is left as it is.

**Model health** (`health.py`): what the router has seen of each model here. Two calls that fail with a model error, run as a different model or are rejected route the model around for a day, then three days, then a week, after which it is tried again. A call that runs as asked clears the record.

**Main-loop effort**: the effort of the conversation's own turn. The mod raises it for one turn when the request profiles as complex or harder, never lowers it, and leaves it alone wherever an effort change would re-read the context uncached.

**Fork**: an agent that starts from its parent's conversation. It runs on its parent's model and reads its parent's cache, so the router leaves it alone. A skill with `context: fork` is not a fork, despite the name ([skills](https://code.claude.com/docs/en/skills.md#run-skills-in-a-subagent)). It starts a new subagent of its `agent` type without an `agent.spawn` event, so it isn't routed either, and its model comes from the skill's `model` field, else from its agent type and the session.

**Redo**: the same call issued again in the same request after its result came back. A redo runs one level higher and counts as a vote that its first level was too low. A run that swapped models never finishes a call, so issuing it again is no redo.

**Model swap**: a run whose model changed mid-run, reported as `modelsUsed` on the `Agent` result ([hooks](https://code.claude.com/docs/en/hooks.md)). A content-classifier fallback causes it: Sonnet 5.5 to Sonnet 5, Opus 5.5 and Fable to Opus 4.8 or Opus 5 ([model config](https://code.claude.com/docs/en/model-config.md), "Automatic model fallback"). Sonnet 5 scored 10.3% against 70.6% on Terminal-Bench 4.0 ([announcement](https://www.anthropic.com/news/claude-sonnet-5-5)). A swapped run is no health evidence, no redo and no count for its level.

**Vote**: evidence that a level was routed too low: a redo, or Claude naming a stronger model than the pick. Enough votes raise the level one step, and a raise that keeps being overruled with a weaker model is undone the same way.

**Intake**: the step that decides whether a prompt or a turn is the person's request and at what level (`intake.py`). A teammate's report, another session's message and a background notification are not. A typed skill is read from its arguments and its body.

**Request**: one prompt from the user and the turns it starts. Redos count only within one request. Its record is kept by `request.py`.

**Ledger** (`ledger.py`): the append-only log of every spawn, result, turn effort and turn's spend, with the reason for each decision. A result row names the models of a swapped run in `models_used`.

**Spend** (`ledger.py`): a request's tokens priced at list price from the registry, summed per turn. A model with no price is counted as unpriced, never priced as its neighbour.

**Gate**: a check before a turn ends (`gates.py`). One asks for a test run when code in the project changed and nothing checked it. The other asks for a stronger agent's review when a weak session model answered a hard request alone. Each asks once and then lets go. Both are on by default; `verify_gate` and `strong_agent_gate` set to `false` turn them off.

**Schema**: the version number written into every state file and allowed in override files. An older file is upgraded as it is read and saved at the current version on its next write. A file from a newer release is read but never overwritten.

**Set aside**: what happens to a state file that can't be read. It is renamed with a `.corrupt-` suffix and a timestamp, routing carries on with that state empty, and `/model-router:models` lists it. Only the three newest copies of each file are kept.

**Upstream**: the public feeds the plugin compares against what was last reviewed: Claude Code releases, the docs index, model aliases and model status.

**Baseline**: a way of running Claude Code without the router that the benchmark compares it against, such as Claude Code's defaults or every subagent forced onto one model.

**Pass check**: a benchmark task's script that decides, from the files and the answer a run left, whether the task was solved.

**Cost per solved task**: a configuration's total spend on a set of runs divided by the runs whose pass check passed. It is the benchmark's measure of cost.

**Pilot**: a benchmark run with too few runs per task to tell a difference from noise. Every row it produces is PARTIAL, and it supports no claim beyond its raw data.
