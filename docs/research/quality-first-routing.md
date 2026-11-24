<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="../README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="model-control.md">Research</a> &nbsp;&middot;&nbsp; Next: <a href="caller-levels-and-redo.md">Redo research</a></p>
<!-- End of page head. -->

# Quality-first routing: what the docs say about saving without losing quality

2026-11-25: the 0.3.0 redesign replaced the conclusions here that rest on the router reading the brief: the `complex` floor for fix and hunt briefs, the `priority` option with its `fast` and `quality` steps, and the main-loop effort raise. Claude now names each call's model and effort from a table in the session note, which puts a bug's cause on Opus at `high`. The [architecture review](architecture-review.md) has the reasons.

Researched 2026-11-23 against Claude Code **2.1.330**, with every page fetched the same day. The rule under test is the user's: "Save only when quality is unaffected. If a saving costs quality, quality wins." What a plugin can control is in [model-control.md](model-control.md); this page reuses its short names. Anything not confirmed in a primary source is marked **UNCONFIRMED**.

Short names used below. Names marked `(new)` are not in model-control.md.

- `[model-config]` https://code.claude.com/docs/en/model-config
- `[sub-agents]` https://code.claude.com/docs/en/sub-agents
- `[hooks]` https://code.claude.com/docs/en/hooks
- `[costs]` https://code.claude.com/docs/en/costs
- `[advisor]` https://code.claude.com/docs/en/advisor
- `[env-vars]` https://code.claude.com/docs/en/env-vars
- `[changelog]` https://code.claude.com/docs/en/changelog
- `[best-practices]` (new) https://code.claude.com/docs/en/best-practices
- `[plat-models]` https://platform.claude.com/docs/en/about-claude/models/overview
- `[plat-choosing]` https://platform.claude.com/docs/en/about-claude/models/choosing-a-model
- `[plat-optimizing]` https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence
- `[plat-effort]` (new) https://platform.claude.com/docs/en/build-with-claude/effort
- `[plat-advisor]` (new) https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool
- `[new-opus]` (new) https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5
- `[new-sonnet]` (new) https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5
- `[new-haiku]` (new) https://platform.claude.com/docs/en/models/haiku-5-5/whats-new-haiku-5-5 and the model page https://platform.claude.com/docs/en/models/haiku-5-5/overview
- `[new-fable]` (new) https://platform.claude.com/docs/en/models/fable-5-2/whats-new-fable-5-2
- `[news-opus]` (new) https://www.anthropic.com/news/claude-opus-5-5
- `[news-sonnet]` (new) https://www.anthropic.com/news/claude-sonnet-5-5
- `[news-haiku]` (new) https://www.anthropic.com/news/claude-haiku-5-5
- `[news-fable]` (new) https://www.anthropic.com/news/claude-fable-and-mythos-5-2
- `[blog-dials]` (new) https://claude.com/blog/claude-model-and-effort-level-in-claude-code (linked from `[model-config]` "Available models")
- `[blog-effort]` (new) https://claude.dev/blog/spending-your-effort/ (linked from `[model-config]` "Choose an effort level")
- `[blog-which]` (new) https://claude.dev/blog/which-claude-model-should-you-build-with/
- `[blog-verify]` (new) https://claude.dev/blog/how-the-claude-code-team-thinks-about-verification/
- `[binary]` the installed 2.1.330 binary and `claude --help`

The blog posts are Anthropic's own but not reference docs; a number that comes only from a blog says so.

## Q1. Model choice and quality

No model newer than Fable 5.2, Opus 5.5, Sonnet 5.5 and Haiku 5.5 is listed `[plat-models]`. Prices are in model-control.md Q5.

What each model is recommended for:

- **Opus 5.5 is the default starting point.** "For most agent workloads, start with Claude Opus 5.5 at its default effort (`medium`)" `[plat-optimizing]#compare-models-on-cost-per-task`; same in `[plat-models]` and `[plat-choosing]`.
- **Fable 5.2 is for when Opus at higher effort falls short.** "If your evals at `xhigh` or `max` effort still fall short on demanding reasoning or long-horizon agentic work, move to Claude Fable 5.2" `[plat-choosing]`. Same wording in `[new-fable]` and `[news-fable]`.
- **Sonnet 5.5 is for well-scoped work** "fixing bugs, and creating polished documents" `[news-sonnet]`. But "A migration or a performance optimization can have clear specs and still be difficult enough to warrant Opus 5.5" `[blog-which]`, and Opus "remains clearly stronger at complex, open-ended work requiring sustained judgment" `[news-sonnet]`.
- **Haiku 5.5 is for narrow, checkable work.** "Sonnet 5.5 and Opus 5.5 remain better choices for complex agentic coding tasks ... Haiku 5.5 is best suited to more narrowly scoped tasks ... like compaction, summarization, or subagent work" `[news-haiku]`. "Keep small models on work where a mistake is cheap to spot, like finding files, running tests and reading logs. Keep code edits and judgment calls on the main model" `[blog-which]`.

Anthropic's own measured numbers that bear on quality loss:

| Measurement | Result | Source |
| :- | :- | :- |
| SWE-bench Pro subset, per solved task | Sonnet 5.5 `high` 87.6% $0.11; Opus 5.5 `low` 87.4% $0.12; Opus 5.5 `medium` 92.8% $0.22; Fable 5.1 `high` 92.3% $1.19 | `[plat-optimizing]#compare-models-on-cost-per-task` |
| Same subset, 463 offline problems | Fable 5.2 `high` 96.3% $1.39, `low` 94.6% $0.39; Opus 5.5 `medium` 93.1% $0.22, `high` 95.5% $0.30, `xhigh` 96.5% $0.75 | `[plat-optimizing]` |
| GPQA Diamond | Haiku 5.5 85%; Opus 5.5 and Sonnet 5.5 91% each | `[plat-optimizing]` |
| Haiku 5.5 subagents under Opus 5.5 `medium` on coding | 32% less per attempt, pass rate about 4 points lower (95% interval 1.3 to 7.1) | `[plat-optimizing]` |
| FrontierCode 2.0 | Fable 5.2 `high` 45.3%, Opus 5.5 best (max) 45.5%, at about half the cost for Fable | `[news-fable]`, `[blog-which]` |
| Terminal-Bench 4.0 | Sonnet 5.5 70.6%, Sonnet 5 10.3% | `[news-sonnet]` |

Two rules from the same page decide whether a saving is real:

- "The bill is decided by the tasks the cheaper model fails, because a failed task still bills its tokens, then the retry, then whatever the failure costs downstream." "Price the tail of your workload, not the median: compare models on the hardest tenth of your tasks" `[plat-optimizing]#compare-models-on-cost-per-task`.
- These results "are Anthropic-internal ... and directional, not guarantees" `[plat-optimizing]`; "benchmark margins have become a less reliable guide to real-world differences" `[news-opus]`.

## Q2. Effort and thinking

- **Thinking is adaptive and steered by effort on all four models.** Opus 5.5 and Fable 5.2 can't turn it off; Haiku 5.5 can at `high` or below `[plat-models]`, `[new-opus]`, `[new-sonnet]`, `[new-haiku]`. In Claude Code, `MAX_THINKING_TOKENS` and `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` don't apply to these models `[model-config]#adaptive-reasoning-and-fixed-thinking-budgets`, so effort is the router's only thinking control.
- **Effort governs more than thinking.** It "affects all tokens in the response", and "lower effort also means fewer and terser tool calls" `[plat-effort]#how-effort-works`; a higher level "tested more edge cases and verified more of its work before answering" `[model-config]#choose-an-effort-level`.
- **Defaults differ.** API: Fable 5.2 and Sonnet 5.5 `high`, Opus 5.5 and Haiku 5.5 `medium` `[plat-models]`. Claude Code: all four `medium` `[model-config]#adjust-effort-level`. A subagent on a model other than the session's uses "that model's saved effort level or its default" from 2.1.317 `[changelog]`.

Per-model guidance (each overrides the generic table, `[plat-effort]#recommended-effort-levels`):

- **Sonnet 5.5**: "start with `medium` for well-specified tasks and move to `high` for harder or longer ones" `[plat-effort]`, `[new-sonnet]`. Measured: "At `medium`, Claude Sonnet 5.5 scored about 7 points lower than at its default, `high`, on the SWE-bench Pro subset for about 59% of the cost per task ... and scored 62.7% compared with 77.8% on the internal agentic-coding benchmark" `[plat-optimizing]#tune-effort`.
- **Haiku 5.5**: "Start with `medium` for most work, including agentic coding. Use `low` ... for chat, short tool tasks, and simple, high-volume requests. In long agent prompts, the model is more likely to skip a search, stop early, or skip a check at `low`" `[plat-effort]`. No published number for the `low` loss was found: **UNCONFIRMED**.
- **Opus 5.5**: on SWE-bench Pro, `medium` is "about 2.5 points lower for about 70% of the cost" of `high`, `low` "about 8 points lower for about a third", `xhigh` "about 1.4 points higher for 2.5 times the cost of `high`" `[blog-which]`. On research and defect search the loss at `low` is large: 13.3 points on DRACO, 34 on an internal defect-search benchmark, against `medium` `[plat-optimizing]#tune-effort`.
- **Fable 5.2**: "Most of the gain in capability is already present at `high`"; "A turn can end before the work is done ... This occurs more often at `low` and `medium` effort" `[plat-effort]`, `[new-fable]`.

When effort helps and when it doesn't:

- Claude Code's table: `medium` "fits day-to-day engineering work with a clear scope, such as implementing a new feature"; `high` is for "work where verification matters or edge cases are likely, such as fixing a bug in an existing codebase" `[model-config]#choose-an-effort-level`. `low` is for "quick exchanges where you review each result"; a subagent has nobody reviewing each step.
- Effort fixes missed edge cases, not a wrong approach: "increasing effort tends to reduce failures due to missing edgecases ... but does not fix when the model has the wrong approach" (`[blog-effort]`, blog only).
- The diagnostic: "If Claude has all the pertinent context, clearly tried, and still got it wrong, that's a signal to pick a more capable model. If Claude got it wrong by skipping a file, not running the tests, or bailing on a refactor partway through, pick a higher effort level" `[blog-dials]`.

**Does a weaker model at higher effort ever match a stronger one?** On some measurements, with limits:

- Opus 5.5 at `low` "solved about the same share of tasks as Sonnet 5.5 at its default, inside run-to-run noise, for about 7% more per solved task" `[plat-optimizing]`. Sonnet 5.5 at Max "even performs comparably to Opus 5.5" on several evaluations, though Opus "remains clearly stronger at complex, open-ended work requiring sustained judgment" `[news-sonnet]`.
- A stronger model at lower effort often beats a weaker one pushed up: Fable 5.2 `high` matched Opus 5.5 `max` on FrontierCode 2.0 at about half the cost. "Past `high`, one more effort level can cost as much as a bigger model" `[blog-which]`. "The larger model can accomplish tasks the smaller one cannot even at the highest effort settings" `[blog-dials]`.

Effort can stand in for model strength on checkable coding benchmarks; no doc says it does on open-ended judgment work.

## Q3. Claude Code controls

Details are in model-control.md Q1 and Q2; only what matters for quality is listed here.

- Model and effort order for a subagent, and the env vars in play, are in model-control.md Q1. The binary contains `CLAUDE_CODE_SUBAGENT_EFFORT`, `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` `[binary]`.
- `opusplan` plans on Opus and executes on Sonnet. Anthropic's guide warns: "That puts your code edits on Sonnet ... Measure opusplan on your own tasks before you make it your default" `[blog-which]`. `--fallback-model` switches only on overload or server errors `[model-config]#fallback-model-chains`. Content-classifier fallback moves a flagged request to an older model (cyber to Opus 4.8 or Sonnet 5, biology to Opus 5, or a refusal on Sonnet 5.5), and `modelsUsed` in the Agent `tool_response` shows a mid-run swap `[model-config]#automatic-model-fallback`, `[hooks]#agent`.
- "Dropping to the smaller model for routine stretches saves real money at no quality cost" `[blog-dials]`; "For simple subagent tasks, specify `model: haiku`" `[costs]`.

## Q4. Quality guards

- **A check the model can run is the first lever, before model or effort.** "Often providing a means of self-verification is more cost effective and adds less latency than changing effort levels. When a model isn't good enough for a task, take these steps in order: Check that the model can verify its own work ... Move to the next model up, starting at a lower effort level than you used on the smaller one" `[blog-which]`. "If it has nothing to run that tells it whether the work actually is done, 'looks done' is all it has to go on" `[best-practices]#give-claude-a-check-to-run`. A separate grader checking against a rubric improved task success by up to 10 points (`[blog-verify]`, blog only).
- **Re-run failures at higher effort, but only with a reliable failure signal.** `low` with failures re-run at `high`: about 97% at about $0.17 per task, against 95.3% at $0.29 with everything at `high`. "A checker that passes bad work lets those failures through" `[plat-optimizing]#re-run-failures-at-higher-effort`.
- **Stop and SubagentStop hooks can refuse to stop.** Both take `decision: "block"` with a `reason`, under the `stop_hook_active` input and an 8-in-a-row cap `[hooks]#stop-decision-control`. SubagentStop input has `agent_type`, `agent_transcript_path` and `last_assistant_message` `[hooks]#subagentstop-input`. Prompt and agent hooks can run an LLM judge there `[hooks]#prompt-based-hooks`. A `CLAUDE.md` line like "Always typecheck before you finish" "works most of the time, not every time" `[best-practices]#enforce-the-check-with-a-stop-hook`.
- **The advisor buys about what more effort buys.** Opus 5.5 `high` with a Fable 5.1 advisor scored 1.7 points higher "at the edge of run-to-run noise, for about 2.1 times the cost"; where the executor rarely consulted, the pairing "scored within run-to-run noise of the executor alone, or lower" `[plat-optimizing]#advisor-strategy-escalate-hard-decisions`. It is a user setting `[advisor]`.
- **A second reviewer is not free quality.** "Adding a second, more expensive, reviewer on top of the primary one is often a worse approach than tuning the primary" (`[blog-verify]`, blog only). A miss from "didn't try hard enough" is fixed by effort, one from "didn't know enough" by a stronger model `[blog-dials]`; the platform's rule: "If you lowered effort, restore it; otherwise try the next tier up at its default effort" `[plat-optimizing]#start-here`.

## Q5. Changes in the last few months

From `[changelog]` 2.1.270 to 2.1.330 (September 14 to November 20, 2026), only what affects model, effort or guards:

- Models: 2.1.280 Opus 5.5, 2.1.284 Sonnet 5.5, 2.1.293 Haiku 5.5, 2.1.321 Fable 5.2. Fast mode on Opus 4.8 is removed 2026-11-30 (2.1.316), and `claude-sonnet-4-5-20250929` retires the same day.
- Controls: 2.1.292 the Agent tool's `effort`; 2.1.296 `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`; 2.1.316 `CLAUDE_CODE_SUBAGENT_EFFORT`; 2.1.317 a subagent on another model uses that model's saved level or default; 2.1.329 `effort` fixed for named teammates.
- Fallback: 2.1.318 subagents stop instead of switching models under "Ask each time"; 2.1.330 a subagent's fallback carries over to the agents it starts; 2.1.327 resumed subagents keep their starting model; 2.1.325 and 2.1.330 subagents with their own model aren't moved to the plan-mode model under `opusplan`; 2.1.300 and 2.1.326 `updatedInput` validated and kept in more multi-hook cases.

## Where the docs disagree

- **Default model for coding.** `[costs]`: "Sonnet handles most coding tasks well and costs less than Opus. Reserve Opus for complex architectural decisions or multi-step reasoning." `[plat-models]`, `[plat-choosing]`, `[plat-optimizing]` and `[blog-which]` say to start most work on Opus 5.5 at `medium`. `[best-practices]` sits between: "the default Opus 5.5 handles most coding well and Sonnet costs less on routine work."
- **`low` for subagents.** The generic effort table lists "subagents" as a `low` use case `[plat-effort]#effort-levels`, but the per-model Haiku guidance, which overrides it, warns that `low` skips searches and checks, and `[best-practices]` ties `low` to a person reviewing each result.
- **Advisor cost.** `[advisor]` says a faster main model with a stronger advisor "typically costs less than running the stronger model throughout"; `[plat-optimizing]` measured Haiku and Sonnet with an Opus advisor on GPQA as no gain for 12% and 25% more.
- **`CLAUDE_CODE_SUBAGENT_EFFORT` version.** `[env-vars]` says v2.1.315; `[changelog]` lists 2.1.316. The 2.1.330 binary has it `[binary]`.
- **Haiku 5.5 safeguards.** `[news-haiku]` says Haiku 5.5 has cybersecurity safeguards that "block penetration testing", but `[model-config]#automatic-model-fallback` lists classifier fallback only for Fable, Opus 5.5, Sonnet 5.5 and Opus 5. What a flagged Haiku 5.5 subagent does in Claude Code is **UNCONFIRMED**.

## What this means for the router

The findings above were written on 2026-11-23 against the settings of that day. Each item below says what the router does now.

### What the router does

- **The router puts no work on Haiku that nothing named.** Haiku runs where a call or an agent's definition names it, where a rule in `model-router.json` sets it, under `CLAUDE_CODE_SUBAGENT_MODEL=haiku`, and on a Haiku session for a call that names no model (a teammate starts on Sonnet there). The session note's table puts Haiku at `medium` on finding, reading and listing, and the scout and verifier pin it for search and for running checks. Nothing in the router keeps a code edit off Haiku when a call names it. Whether Claude, following the table, keeps code edits and judgment calls off Haiku ("keep code edits and judgment calls on the main model" `[blog-which]`, against the measured 4-point loss `[plat-optimizing]`) has not been benchmarked. `max_tier: haiku` caps every call there; that is the user's choice.
- **Only `frontier` asks for Fable.** `config/models.json` gives `needs: 4` to `frontier` alone, and the session note's table names Fable only for open-ended research, matching "move to Fable when Opus at higher effort still falls short" `[plat-choosing]`. A redo of an Opus run at `high` or above also reaches it (`router._redo`).
- **Effort is set when a call or its agent names a model and nobody chose an effort**: not the caller, the agent or the user's environment (`router.decide`, `ladder.effort_on`). It is never below the session's own effort on the session's model (`router._session_floor`).
- **No level moves on its own.** `learn.py`, which raised a level after enough redos, was removed. A redo climbs for that call only, `/model-router:usage` counts redos by level, and the user can raise a level under `levels` in `model-router.json`. A raised level sets only the effort of a call that names a model and no effort, and the picks of a rule's `level`, a teammate's start and a redo's next model; only a rule's `level` raises the model a call names.
- **The redo climbs, effort before model.** Only a call Claude marks "Redo:" is a redo. With no model or effort named, `router._redo` runs it one effort step up while the run it redoes was below `high`, else on the next stronger model. Claude's judgment triggers it, so it isn't the measured "re-run failures at a higher setting" policy, which needs a check's failure signal `[plat-optimizing]`.
- **No stronger-agent check.** The strong-agent gate, which held a stop on a wording estimate of the request, was removed. The only Stop check is `verify_gate` (`gates.before_stop`). The router gives no `opusplan` or advisor advice; both measured as small or uncertain gains `[blog-which]`, `[plat-optimizing]`.

### Quality risks found, and where they stand

| Risk found | Now |
| :- | :- |
| `standard` runs Sonnet at `medium` and bug fixes landed there, though Claude Code puts "fixing a bug in an existing codebase" at `high` `[model-config]` and Sonnet `medium` measured 7 to 15 points below `high` on coding `[plat-optimizing]` | Open. The session note's table names Opus at `high` for finding a bug's cause and other work that is hard to check, but no row names a fix, so a fix to a clear brief reads as everyday work, Sonnet at `medium`, and nothing in the router raises it. `standard` is still Sonnet at `medium` |
| The verify gate was off, and the cheap first pass needs a reliable failure signal `[plat-optimizing]#re-run-failures-at-higher-effort` | `verify_gate` is on by default (`settings.py`, `plugin.json`) |
| The checker ran at Haiku `low`, which skips checks in long agent prompts `[plat-effort]` | The verifier and scout agents and `levels.lookup` run Haiku at `medium` |
| `complex` stayed on Sonnet, though migrations and performance work are Opus work even with a clear spec `[blog-which]` | `complex` is `needs: 3` at `high` (Opus); `hardest` is Opus at `xhigh` |
| A content fallback was invisible to the ledger: Sonnet 5 scored 10.3% to Sonnet 5.5's 70.6% on Terminal-Bench 4.0 `[news-sonnet]` | `hooks.post_agent` reads `modelsUsed`; a swapped run is its own result (`calls.SWAPPED`), is in the ledger, and Claude gets a note that its result needs a check |
| `priority: fast` drops a step of model and sets no effort above `low` (`registry.select`, `ladder.effort`). The documented way to buy speed at the same quality is fast mode on Opus 5.5 or Fable 5.2, at twice the price `[plat-optimizing]#show-the-model-elapsed-time` | Gone with the `priority` option |
| `main_effort` is off by default, so a hard request on an Opus session runs the main loop at `medium`; raising effort keeps the cache `[plat-effort]#change-effort-mid-conversation` | The option was removed with the request profile. The router leaves the main loop's effort alone; for a hard request the person types `ultrathink` or uses `/effort` |

### Recommendations, ranked

1. **Turn the verify gate on by default** (`settings.py` `verify_gate`, `gates.unverified_edit`). Every cheap-first saving depends on a check that catches failures `[plat-optimizing]#re-run-failures-at-higher-effort`, `[best-practices]#give-claude-a-check-to-run`. Status: shipped.
2. **Run bug fixes and changes to existing code at `high`** `[model-config]#choose-an-effort-level`, `[best-practices]#change-the-model-and-effort`, `[plat-optimizing]#tune-effort`. The smaller option, `levels.standard.effort` at `high`, costs about 1.7 times as much per Sonnet task. Status: not shipped. The session note's table names Opus at `high` for finding a bug's cause, but no row names a fix or a change to existing code, so either one with a clear brief reads as everyday work, Sonnet at `medium`.
3. **Run the verifier, scout and `lookup` at Haiku `medium`**, its documented starting point `[plat-effort]`. Haiku cost is close to nothing: the scout cost $0.0019 to $0.0024 per 70-run bench configuration, measured before the move from `low` to `medium` (`docs/BENCHMARKS.md` "Setup"). Status: shipped.
4. **Move `complex` to Opus.** On SWE-bench Pro, Opus `medium` solved 92.8% against Sonnet `high`'s 87.6%, at twice the cost per solved task `[plat-optimizing]`; migrations and performance work "warrant Opus 5.5" `[blog-which]`. The one recommendation with a clear cost; under the user's rule the docs support paying it. Status: shipped, then raised to Opus at `high`.
5. **Check a routed subagent's own edits at SubagentStop.** When a routed agent that edits code ran no check, return `decision: "block"` once with a reason to run the project's checks `[hooks]#stop-decision-control`; that is cheapest inside the subagent that made the change. Status: not shipped (`hooks.agent_stopped` only notes the checks via `gates.note_agent_checks`).
6. **Treat a mid-run model swap as its own result** (`hooks.post_agent`). Status: shipped.
7. **Turn `main_effort` on where the cache survives.** Status: dropped. The option was removed with the request profile, so the router no longer changes the main loop's effort.
8. **Say plainly that `priority: fast` trades quality**, and point to fast mode as the documented way to buy speed at the same quality (`docs/GUIDE.md`, `/model-router:*` output). Status: dropped with the `priority` option.
9. **Pick effort or model on a redo by the failure kind.** A run that skipped checks should redo at more effort on the same model; one that tried and was wrong should redo on the next model `[blog-dials]`, `[blog-effort]`. The router can't read the failure kind, so the caller picks the lever. Status: shipped: a redo that names its own effort or model runs as named.

## Open questions

- No doc measures Sonnet 5.5 `medium` against `high` on short, well-specified bug fixes, the router's most common `standard` task. The 7-point figure is on SWE-bench Pro, which is harder than the bench here (`docs/BENCHMARKS.md`). **UNCONFIRMED** how much of it applies.
- The size of Haiku 5.5's loss at `low` against `medium` on search or check tasks: no number in the fetched pages. **UNCONFIRMED**.
- Which content-fallback target is weaker on security work, Sonnet 5 or Opus 4.8: no doc compares them. That decides whether routing security review to Opus also protects the fallback case. **UNCONFIRMED**.
- Whether a plugin's SubagentStop `decision: "block"` works for background subagents and teammates as for foreground ones. Not tested.
- Whether the Sonnet 5.5 effort numbers were taken with Claude Code's system prompt or the bare API. `[plat-optimizing]` describes its runs as "priced as a customer is billed"; the harness is not named. **UNCONFIRMED**.
