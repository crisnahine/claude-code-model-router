# Workflow stages, ultracode and agent teams

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.3.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

Checked 2026-11-24 against Claude Code 2.1.331 (`claude --version`). Docs pages fetched the same day as `.md`. Code read in the `redo-fixes` worktree. This page builds on `docs/research/model-control.md`, `quality-first-routing.md` and `caller-levels-and-redo.md` and does not repeat their findings on ordinary subagents.

## What we built

- `hooks/router.ts`, `register`, `agent.spawn` hook: every non-fork, non-resume spawn goes to `mod-spawn` with `kind` set to `workflow` (`e.workflow`), `teammate` (`e.isTeammate`) or `subagent`. A workflow agent's `tool_use_id` is left out, since all agents of a run share it. The pick's `model` goes to `next`, its effort waits in `agentEffort` by `agentId`.
- `router.ts` `turn.step` hook: for any agent with a remembered effort, the step's effort is replaced whenever it differs. Only the main loop is raise-only. `inFlight` (keyed by prompt) makes copies of one brief take the same effort on their first request.
- `spawn.facts`: a workflow stage or a teammate role with no agent file gets a stand-in definition with no model, so it is routed by its brief (`profile_call`). A workflow stage is never a redo (`marked` and `earlier` are skipped). `session_effort` is passed only for a call from the main conversation.
- `router.decide`: a model the script or call names is kept, and because `caller` is not in `config.LEVEL_SOURCES`, its effort is left alone too. `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` (`settings.workflow_model`) takes the `_pinned` branch: the model stays, the router sets only effort.
- `router._session_floor`: on the parent's own model, an effort the router sets is raised to the session's level when that is higher.
- `learn.counted` and `learn.spawned`: only `kind == "subagent"` counts toward or votes on a level. Teammates and workflow stages never vote.

## What the sources say

Who decides, per kind of work:

- Workflows put the plan in the script. The comparison table's row "Who decides what runs next" reads "Claude, turn by turn" for subagents, "The lead agent, turn by turn" for agent teams, and "The script" for workflows. https://code.claude.com/docs/en/workflows (section "When to use a workflow")
- "Claude Code picks each workflow agent's model in the same order it uses for subagents. A model the script names for a stage counts as the per-invocation model in that order. When nothing else assigns one, the agent runs on your session's model." https://code.claude.com/docs/en/workflows (section "Cost")
- The docs' cost advice for workflows puts the per-stage choice with the person, through the script: "Ask Claude to use a smaller model for stages that don't need the strongest one when you describe the task." Same section.
- A mod may override the per-invocation model: "If an installed mod sets a model in its `agent.spawn` hook, Claude Code uses that model in place of the per-invocation parameter." https://code.claude.com/docs/en/sub-agents (section "Choose a model"). For workflow agents this came in 2.1.300: "Added support for rewriting a workflow agent's `prompt` and `model` in the `agent.spawn` mod hook, which could only refuse workflow agents before." https://code.claude.com/docs/en/changelog
- The mods reference lists `agent.spawn` for "A subagent or an agent team teammate", and does not name workflow agents. https://code.claude.com/docs/en/plugins/mods/reference (section "Subagents"). The generated type file does: "A subagent, a teammate, a workflow agent or a resumed run", and for `workflow`: "A rewrite of its `prompt` and `model` applies; `description`, `subagentType`, `background` and `cwd` do not apply to it." Type file `plugins/model-router/.claude-plugin/types/claude-code/index.d.ts` (`AgentSpawnInput`, written by Claude Code 2.1.330, in the main checkout; the worktree has no copy).

What the script author is told. The bundled `/workflow-authoring` skill is the reference the workflows page names: "run the `/workflow-authoring` bundled skill to load the script-writing reference Claude works from" (https://code.claude.com/docs/en/workflows, section "Edit a saved script"). It is shipped text in 2.1.331, not a docs page. It says of `agent()`:

- `opts.model`: "Default to omitting it", "the agent inherits the main-loop model (the resolved session model), which is almost always correct. Only set it when you're highly confident a different tier fits the task; when unsure, omit."
- `opts.effort`: "omit to inherit the session effort; use 'low' for cheap mechanical stages and higher tiers only for the hardest verify/judge stages."
- Under ultracode: "token cost is not a constraint."

So an omitted model or effort on a stage is the authoring default ("almost always correct"), and a set effort is a deliberate choice for that stage.

What a mod can and cannot see:

- `AgentSpawnInput` has no effort field (type file). The script's `opts.effort` never reaches `agent.spawn`.
- `turn.step` carries it: `effort` is "How hard the request asks the model to think: the level its loop, `CLAUDE_CODE_EFFORT_LEVEL`, turn or session set, or the model's default." Type file, `TurnStepInput`.
- A stage's prompt can be a stand-in: "A workflow agent's is a fixed placeholder on a run that keeps its script out of SDK events (Code Review's, say): a hook has not seen the prompt, so a rewrite of it is not applied." Type file, `AgentSpawnInput.prompt`. A brief scored from that text says nothing about the stage.
- Resume is keyed before hooks: "The journal knows an agent by what the script asked, before any hook." Type file, `AgentSpawnInput.workflow.agentIndex`. A mod's model change does not break a resume's cache hits.
- No field in the type file names ultracode. Whether ultracode is on is not visible to a mod (not documented).

Ultracode:

- "Ultracode is a Claude Code setting that turns on automatic workflow orchestration for the session, at whichever effort level the session runs at." `claude --effort ultracode` "also sets the effort level to `xhigh`." https://code.claude.com/docs/en/workflows (section "Let Claude decide with ultracode")
- "each request uses more tokens and takes longer than the same request without a workflow", and with it on the `Large workflow` warning and the concurrent subagent limit are off, because "Turning ultracode on already opts you in to large runs." Same section.
- "Turning ultracode on or off with `/effort` or the `ultracode` setting leaves the effort level unchanged." https://code.claude.com/docs/en/model-config (section "Adjust effort level")
- That sentence is about the session's level. It does not say each stage runs at the session's level; that comes from the subagent default below and from the authoring skill.

Effort of stages and teammates, untouched:

- "With none of these, a subagent on the main conversation's model runs at the level you pick in the session, such as with `/effort`, or set with `--effort`. On another model, that level doesn't apply." https://code.claude.com/docs/en/sub-agents (section "When a subagent has no effort field or parameter")
- "On the main conversation's model, a subagent or teammate with no other level set runs at the level you pick in the session or set with `--effort`. On another model, that level doesn't apply." https://code.claude.com/docs/en/model-config (section "Effort level of subagents and teammates")
- "By default, a teammate on the lead's model inherits the lead's effort level." https://code.claude.com/docs/en/agent-teams (section "Specify teammates and models")
- 2.1.317: "Changed subagents and teammates on a different model from the main conversation to use that model's saved effort level or its default, instead of the session's effort level." https://code.claude.com/docs/en/changelog
- `CLAUDE_CODE_SUBAGENT_EFFORT` covers "subagents and workflow agents" and "doesn't apply to forks, forked skills, or teammates." https://code.claude.com/docs/en/env-vars

Prompt cache in a fan-out:

- "Two agents that run with the same model, effort level, agent type, tools, output schema, and working directory build the same tools-and-system-prompt prefix, so an agent that starts after a matching sibling's response has begun reads that sibling's cache on its first request." https://code.claude.com/docs/en/workflows (section "Prompt caching in a fan-out"). The key does not include the prompt text.
- "When a fan-out starts several matching agents at once, Claude Code holds all but the first until the first agent's response begins", capped at `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS`, 5000 ms by default. Same section. Added in 2.1.229: "stagger same-prefix sibling agents so subsequent agents read the cached prompt prefix instead of re-paying it". https://code.claude.com/docs/en/changelog
- "each model has its own cache." https://code.claude.com/docs/en/prompt-caching
- Workflow agents and in-process teammates get the five-minute TTL: they fall "outside the main conversation's cache TTL bucket, so its cache holds for five minutes by default, including on a Claude subscription." https://code.claude.com/docs/en/workflows and https://code.claude.com/docs/en/agent-teams (section "Token usage")
- Prices: Opus 5.5 "$4 / MTok" input, "$5 / MTok" 5m cache write, "$0.20 / MTok" cache hit; Sonnet 5.5 $2, $2.50, $0.10; Haiku 5.5 up to 100K tokens $0.10, $0.125, $0.01. https://platform.claude.com/docs/en/about-claude/pricing

What mixed levels in one fan-out cost. Each distinct (model, effort) pair in a stage is its own prefix, so each extra pair pays one 5-minute write of the prefix where it could have read it. Per extra group that is prefix tokens x (write price minus hit price): $4.80 per MTok on Opus 5.5, $2.40 on Sonnet 5.5, $0.115 on Haiku 5.5. The prefix size of a workflow agent is not documented. The repo's own live record (not a source) saw copies of one lookup read 11,764 cache tokens on their first request (`docs/ARCHITECTURE.md`, "Verified live"). At that size one extra group costs about $0.056 on Opus 5.5, $0.028 on Sonnet 5.5 and $0.0014 on Haiku 5.5. That is small next to a stage's own work. The bigger effect is outside the cache: with levels scored per item, the items of one `pipeline()` can land on different models, so one stage does not do the same work across its items.

Agent teams:

- Teammate model order: "The model your spawn prompt names for that teammate", the definition's `model`, `CLAUDE_CODE_SUBAGENT_MODEL`, "The lead's current model." "If an installed mod sets a model in its `agent.spawn` hook, Claude Code uses that model in place of the first source." https://code.claude.com/docs/en/agent-teams (section "Specify teammates and models")
- "A teammate's model and fast mode are fixed when it spawns." Same page.
- A teammate gets more work after it spawns: "The lead creates tasks and teammates work through them", and "Having 5-6 tasks per teammate keeps everyone productive." Same page. So the spawn brief is only the first of its tasks.
- Cost guidance: "Use Sonnet for teammates. It balances capability and cost for coordination tasks." and "Keep teams small." https://code.claude.com/docs/en/costs (section "Agent team token costs"). Also "Agent teams use approximately 7x more tokens than standard sessions when teammates run in plan mode." Same page.
- `turn.step` reaches in-process teammates: `e.agentId` is "set when a subagent or an in-process teammate makes the call". https://code.claude.com/docs/en/plugins/mods/reference. Split-pane teammates are not named there.

`CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`:

- 2.1.296: "Added `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` to run every workflow agent on one model while other subagents keep theirs." https://code.claude.com/docs/en/changelog. It is still not in https://code.claude.com/docs/en/env-vars, the workflows page or the model-config page.

## Verdict

Partly right. Routing workflow stages and teammates through `agent.spawn`, keeping a script-named model and effort, honoring `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`, never redoing a stage, and keeping teammates out of the votes all match the sources or sit where they are silent. The session-effort floor is our own rule, but it encodes the documented default (an untouched agent on the session's model runs at the session's level), so it is sound. What is wrong is that the router overrides choices the sources give to the script: it rewrites a stage's effort at `turn.step` even when the script set it (the spawn event cannot show it), and it moves stages with no named model below the session's model from a regex score of a brief that may be a placeholder, against the authoring default ("almost always correct") and, under ultracode, against "token cost is not a constraint". For teammates, routing a long-lived agent by its first brief alone can land it on Haiku, below the docs' own Sonnet advice, for its whole life.

## What to change

1. **Never route a workflow stage below its parent's model.** In `router.decide`, when `facts.workflow` and no model is named, hold `target` at `facts.parent` if `reg.stronger(facts.parent, target)`; a pick above the parent stays. Touches `scripts/modelrouter/router.py` (`decide`), `tests/test_model_router.py` (`test_workflow_stages_and_teammate_roles_without_a_file_are_routed`, `test_copies_of_one_brief_launched_in_the_background_all_run_at_its_level`), `docs/ARCHITECTURE.md` (routing order and the ultracode live notes), `docs/GUIDE.md`. Deletes the down-routing of stages, including the case where a placeholder prompt is scored. Backed by https://code.claude.com/docs/en/workflows ("The script" decides; the cost section puts smaller models with the person) and the bundled `/workflow-authoring` text on `opts.model`; the placeholder risk by the type file's `AgentSpawnInput.prompt`. Judgment: no source forbids a mod from lowering a stage. Cost: stages that ran on Haiku or Sonnet under an Opus or Fable session now run on the session's model.
2. **Never lower a workflow stage's effort at `turn.step`.** In `hooks/router.ts`, remember workflow agent ids in `agent.spawn` (beside `agentType`), and in `turn.step` apply the router's effort to those agents only when it is above `e.effort`, as the main loop already does. Touches `hooks/router.ts` (`register`, `agent.spawn` and `turn.step` hooks), `tests/mod/router.test.ts`. Deletes the override of a script's `opts.effort` and the caveat in `docs/CLAUDE_CODE_DOCS.md` ("A script's `effort` on `agent()` isn't on the spawn event, so on a stage that names no model the router sets its own effort over it"). Backed by the type file (`AgentSpawnInput` has no effort; `TurnStepInput.effort` is "the level its loop ... set") and the `/workflow-authoring` text on `opts.effort`. Judgment: the sources allow a mod to rewrite effort; they do not say a script's effort must win. Known cost: a script's `low` on a mechanical stage can still be raised; quality ranks first.
3. **Start teammates no lower than standard.** In `spawn.facts`, set `floor` to at least `Level.standard` when the spawn is a teammate, so a teammate with no model named runs on Sonnet or stronger. Touches `scripts/modelrouter/spawn.py` (`facts`), `tests/test_model_router.py`, `docs/ARCHITECTURE.md` (role floors). Deletes nothing. Backed by https://code.claude.com/docs/en/costs ("Use Sonnet for teammates") and https://code.claude.com/docs/en/agent-teams ("A teammate's model and fast mode are fixed when it spawns"; teammates work through tasks the lead creates later). Judgment: the docs give advice, not a rule.
4. **Correct the docs page that cites these sources.** In `docs/CLAUDE_CODE_DOCS.md`: the ultracode row says ultracode "runs substantive tasks as workflows at the session's effort level", but the model-config text says ultracode works at whatever level the session runs at; the stage-level rule comes from the subagent and teammate effort default and the authoring skill. The fan-out row should list the docs' full key (model, effort, agent type, tools, output schema, working directory) and say that copies of one brief is a narrower match, since a `pipeline()` gives each item its own prompt. Also say the mods reference names only subagents and teammates for `agent.spawn` while the type file and 2.1.300 changelog cover workflow agents. Touches `docs/CLAUDE_CODE_DOCS.md` (and `docs/TRACEABILITY.md` rows that quote it). Deletes the misread. Required by the sources: https://code.claude.com/docs/en/model-config (section "Adjust effort level"), https://code.claude.com/docs/en/workflows (section "Prompt caching in a fan-out").

Kept as they are: `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` handling (`router.decide` `_pinned` branch), "a workflow stage is never redone" (`spawn.facts`), "teammates never vote" (`learn.counted`), keeping a script-named model and its effort (`LEVEL_SOURCES` without `caller`), and `_session_floor`.

## What the sources do not say

- Whether a plugin should change a workflow stage's model at all. The docs allow it (sub-agents "Choose a model", changelog 2.1.300) and never advise for or against it.
- Whether a mod can tell that a stage's `turn.step` effort came from the script's `opts.effort` rather than the session. Not documented; item 2 avoids needing to know.
- How to recognize the placeholder prompt the type file mentions, or which runs other than Code Review's use it. Not documented.
- Whether ultracode is on, from a mod or a hook. Not documented; no type file field names it.
- The size of a workflow agent's tools-and-system-prompt prefix. Not documented; the figure above is the repo's own live record.
- Whether the stagger hold works per matching group when one fan-out has agents of several (model, effort) pairs, or only for the first group. Not documented.
- Whether, on models where an effort change keeps the main conversation's cache (https://code.claude.com/docs/en/prompt-caching, section "Changing effort level"), two sibling stages at different efforts still share a prefix. The workflows page lists effort as part of the key; nothing says the per-message rule applies to a stage's first request.
- Where `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` sits against a mod's `agent.spawn` model. Only the repo's live run on 2.1.331 says the variable wins.
- Whether `agent.spawn` and `turn.step` reach split-pane teammates, which run as separate Claude Code instances. The mods reference names in-process teammates only for `e.agentId`.
- Whether a teammate's later tasks should change its effort. "A teammate's model and fast mode are fixed when it spawns" says nothing about effort per task.
- Any cost or quality measurement of a router-chosen model for workflow stages or teammates against the session's model. No Anthropic page measures it; the 7x figure for teams is for plan mode only.
