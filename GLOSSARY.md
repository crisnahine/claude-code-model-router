# Glossary

The words this plugin's code, docs and reviews use for its own domain.

**Call**: one request to start an agent: a subagent, a teammate or a workflow stage. A call is known by its agent type and its prompt, so the same call issued twice is recognized.

**Spawn**: the moment a call starts an agent. The mod sees it as the engine's `agent.spawn` event, the fallback as a `PreToolUse` hook on the `Agent` tool.

**Path**: how a call reaches the router. The **mod path** runs in `hooks/router.ts` on engine events. The **fallback path** rewrites the `Agent` tool call and stands down when the mod runs.

**Level**: how much reasoning a call needs: lookup, standard, complex, hardest or frontier. It comes from the call's brief and the agent's name.

**Ladder**: the rule that turns a level, the priority and learned feedback into a model and an effort.

**Pick**: the model and effort the ladder gives a call before anyone else's choice is applied.

**Pin**: a model an agent's own definition names. A pinned agent keeps its model, and gets effort from its level when it names none.

**Cap**: the strongest model the user allows (`max_tier`). Every result is lowered to fit it, never raised.

**Redo**: the same call issued again in the same request after its result came back. A redo runs one level higher and counts as a vote that its first level was too low.

**Vote**: evidence that a level was routed too low: a redo, or Claude naming a stronger model than the pick. Enough votes raise the level for good.

**Request**: one prompt from the user and the turns it starts. Redos count only within one request.

**Ledger**: the append-only log of every spawn, result, turn effort and turn's spend, with the reason for each decision.

**Upstream**: the public feeds the plugin compares against what was last reviewed: Claude Code releases, the docs index, model aliases and model status.

**Baseline**: a way of running Claude Code without the router that the benchmark compares it against, such as Claude Code's defaults or every subagent forced onto one model.

**Pass check**: a benchmark task's script that decides, from the files and the answer a run left, whether the task was solved.

**Cost per solved task**: a configuration's total spend on a set of runs divided by the runs whose pass check passed. It is the benchmark's measure of cost.
