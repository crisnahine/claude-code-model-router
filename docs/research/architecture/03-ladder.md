# The five-level ladder and its model and effort per level

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.2.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

Checked 2026-11-24 against Claude Code 2.1.331 (`claude --version`), with every page below fetched the same day as `.md` (or as HTML for the anthropic.com posts). This page builds on `docs/research/model-control.md`, `docs/research/quality-first-routing.md` and `docs/research/caller-levels-and-redo.md` and does not repeat what they settled; it re-reads the numbers they quote and adds what they missed.

## What we built

- `plugins/model-router/config/models.json` `levels`: `lookup` needs strength 1 at `medium`, `standard` 2 at `medium`, `complex` 3 at `high`, `hardest` 3 at `xhigh`, `frontier` 4 at `high`. `models` ranks haiku, sonnet, opus, fable 1 to 4 on `strength`, `cost` and `speed` alike.
- `config.Level` is the five names in order; `Level.up()` is the redo climb.
- `registry.Registry.select(needs, priority, usable)`: `balanced` takes the cheapest usable model at or above `needs`; `quality` first moves `needs` one strength step up, `fast` one step down and then takes the quickest. On the shipped ranks cost and speed are both in strength order, so every priority ends up on the one model at the shifted strength.
- `ladder.effort`: the level's own effort; under `fast` it is dropped unless it is `low`.
- `ladder.effort_on`: one more effort step, at most `xhigh`, when the cap, allowlist or health left only a model weaker than the level's `needs`.
- `ladder.pick` joins the two, after a learned raise from `learn.raises()`.
- Shipped picks (`test_the_shipped_ladder_picks_at_balanced_priority`): haiku medium, sonnet medium, opus high, opus xhigh, fable high. Under `quality`: sonnet medium, opus medium, fable high, fable xhigh, fable high. Under `fast`: haiku, haiku, sonnet, sonnet, opus, each with no effort set.
- The role agents `scout`, `verifier` and `docs-researcher` pin `model: haiku` and `effort: medium` in their own frontmatter (`plugins/model-router/agents/*.md`).

## What the sources say

Sources, short names used below:

- `[plat-effort]` https://platform.claude.com/docs/en/build-with-claude/effort
- `[plat-models]` https://platform.claude.com/docs/en/about-claude/models/overview
- `[plat-choosing]` https://platform.claude.com/docs/en/about-claude/models/choosing-a-model
- `[plat-optimizing]` https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence
- `[plat-pricing]` https://platform.claude.com/docs/en/about-claude/pricing
- `[prompt-opus]` https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5
- `[prompt-sonnet]` https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5
- `[prompt-haiku]` https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5
- `[prompt-fable]` https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2
- `[new-fable]` https://platform.claude.com/docs/en/models/fable-5-2/whats-new-fable-5-2
- `[model-config]` https://code.claude.com/docs/en/model-config
- `[sub-agents]` https://code.claude.com/docs/en/sub-agents
- `[best-practices]` https://code.claude.com/docs/en/best-practices
- `[costs]` https://code.claude.com/docs/en/costs
- `[changelog]` https://code.claude.com/docs/en/changelog
- `[news-sonnet]` https://www.anthropic.com/news/claude-sonnet-5-5
- `[news-haiku]` https://www.anthropic.com/news/claude-haiku-5-5
- `[news-fable]` https://www.anthropic.com/news/claude-fable-and-mythos-5-2

### Strengths and prices

- The order Haiku, Sonnet, Opus, Fable is documented: "From lowest to highest cost and capability, the current models are Claude Haiku 5.5, Claude Sonnet 5.5, Claude Opus 5.5, and Claude Fable 5.2 (the frontier model)" `[plat-optimizing]#trade-cost-against-intelligence`.
- List prices per MTok, input and output: Fable 5.2 $8 and $40, Opus 5.5 $4 and $20, Sonnet 5.5 $2 and $10, Haiku 5.5 "From $0.10" and "From $0.50" `[plat-models]`. Haiku 5.5 over 100,000 prompt tokens pays $0.50 and $2.50 `[plat-pricing]`, still a quarter of Sonnet. Each step up roughly doubles the token price above Sonnet, and Sonnet is 20 times Haiku.
- What each is for, in the docs' words `[plat-models]`: Fable "For demanding reasoning and long-horizon agentic work"; Opus "For long-running agentic coding and knowledge work"; Sonnet "The best combination of speed and intelligence"; Haiku "For high-volume, latency-sensitive tasks such as classification, extraction, and routing".
- Sonnet: "strongest at well-scoped everyday tasks, fixing bugs, and creating polished documents, slides, and spreadsheets", while "Opus 5.5 remains clearly stronger at complex, open-ended work requiring sustained judgment" `[news-sonnet]`.
- Haiku: "best suited to more narrowly scoped tasks ... like compaction, summarization, or subagent work", and "Sonnet 5.5 and Opus 5.5 remain better choices for complex agentic coding tasks" `[news-haiku]`. "High-volume work with outputs you can check: Try Claude Haiku 5.5 first" `[plat-optimizing]#start-here`.
- Fable: "For most coding and knowledge tasks, we recommend that users start with Claude Opus 5.5 ... and then switch to a different model as required ... upward to Fable or Mythos, or downward to Claude Sonnet 5.5 or Claude Haiku 5.5" `[news-fable]`. "If your evals at `xhigh` or `max` effort still fall short on demanding reasoning or long-horizon agentic work, move to Claude Fable 5.2" `[plat-choosing]`.
- The start point the docs name most: "For most agent workloads, start with Claude Opus 5.5 at its default effort (`medium`)" `[plat-optimizing]#compare-models-on-cost-per-task`. Claude Code's own pages are softer on Sonnet: "the default Opus 5.5 handles most coding well and Sonnet costs less on routine work" `[best-practices]#change-the-model-and-effort`; "Sonnet handles most coding tasks well and costs less than Opus" `[costs]`.

### Measured cost and score

All Anthropic-internal and "directional, not guarantees" `[plat-optimizing]#start-here`.

| Setup | Score | Cost | Source |
| :- | :- | :- | :- |
| Sonnet 5.5 `high` | 87.6% solved | $0.11 per solved task | `[plat-optimizing]#compare-models-on-cost-per-task` |
| Opus 5.5 `low` | 87.4% | $0.12 per solved | same |
| Opus 5.5 `medium` | 92.8% | $0.22 per solved, $0.21 per task | same, and `#tune-effort` |
| Opus 5.5 `high` | 95.3% | $0.29 per task | `#tune-effort` |
| Opus 5.5 `xhigh` | 96.7% (a single run) | $0.74 per task | `#tune-effort` |
| On 463 problems: Opus 5.5 `high` / `xhigh`, Fable 5.2 `low` / `high` | 95.5% / 96.5%, 94.6% / 96.3% | $0.30 / $0.75, $0.39 / $1.39 per solved | `#compare-models-on-cost-per-task` |
| Sonnet 5.5 `medium` against its `high` | "about 7 points lower ... for about 59% of the cost per task"; 62.7% against 77.8% on the internal agentic-coding benchmark | | `#tune-effort` |
| Internal agentic coding: Opus 5.5 `medium` / Fable 5.2 `medium` | 86.6% / 88.9% (a single run) | $0.84 / $1.34 per attempt | `#compare-models-on-cost-per-task` |
| DeepResearch Bench II, default effort | Fable 5.2 "cost 4.3 times as much as Claude Sonnet 5.5 for no measurable gain in score, and 2.9 times as much as Claude Opus 5.5 for a score 1.9 points lower" | | same |
| Defect search, alone | Fable 5.2 from `low` to `high` "scored 1.6 to 5.9 points higher than Claude Opus 5.5 alone at `high`, for 1.5 to 2.4 times the cost" | | `[plat-optimizing]`, the section on delegating bulk work |
| Haiku 5.5 `medium` | 85% on GPQA Diamond against 91% for Opus and Sonnet; 93% on a 79-task Managed Agents set, about $0.011 per passed attempt | | `#compare-models-on-cost-per-task` |
| Research at `low` | Opus 5.5 13.3 points below `medium` on DRACO, 34 points below on defect search | | `#tune-effort` |

The rules the same page sets for reading these: "Compare models on cost per completed task, not per token"; "Price the tail of your workload, not the median: compare models on the hardest tenth of your tasks" `[plat-optimizing]#compare-models-on-cost-per-task`.

### Default effort per model

- API: "Most Claude models default to high effort ...; Claude Opus 5.5 and Claude Haiku 5.5 default to medium" `[plat-effort]#how-effort-works`. The table in `[plat-models]` lists Fable 5.2 `high`, Opus 5.5 `medium`, Sonnet 5.5 `high`, Haiku 5.5 `medium`.
- Claude Code: "Fable 5.2, Opus 5.5, Sonnet 5.5, and Haiku 5.5 default to `medium`, and Opus 4.7 defaults to `xhigh`. Other models in the effort levels table default to `high`" `[model-config]#adjust-effort-level`. So in Claude Code, Fable and Sonnet run one level below their API default unless something sets effort.
- "Setting `effort` to the model's default ... produces exactly the same behavior as omitting the `effort` parameter entirely" `[plat-effort]#how-effort-works`.

### Efforts each model accepts

- Claude Code: "Fable models" and "Opus 5.5, Sonnet 5.5, Haiku 5.5, Opus 5, Sonnet 5, Opus 4.8, and Opus 4.7" take `low`, `medium`, `high`, `xhigh`, `max`; "Opus 4.6 and Sonnet 4.6" take all but `xhigh`; "Models not listed here do not support effort". "If you set a level the active model does not support, Claude Code falls back to the highest supported level at or below the one you set" `[model-config]#adjust-effort-level`.
- The API list agrees, and adds: "Not every model that supports `max` supports `xhigh`" `[plat-effort]#effort-levels`. Sonnet 4.5 and Haiku 4.5 are on neither list.
- The aliases the ladder sends resolve to older models off the Anthropic API: on Bedrock and Agent Platform `sonnet` is Sonnet 4.5 and `haiku` Haiku 4.5; on Foundry `opus` is Opus 4.6, `sonnet` Sonnet 4.5, `haiku` Haiku 4.5 `[model-config]#model-aliases`. There the `lookup` and `standard` efforts have no effect, and `hardest`'s `xhigh` runs as `high` on Foundry. The mod sends the newest ID on those providers (`newest`, `older_aliases` in `models.json`); the settings-hook fallback can't.
- "The effort scale is calibrated per model, so the same level name does not represent the same underlying value across models" `[model-config]#choose-an-effort-level`. "Effort levels rank the same way across Claude models, but the amount of thinking at each level can differ" `[prompt-fable]#calibrate-effort`.

### Recommended effort per model

The generic table names `low` for "Simpler tasks that need the best speed and lowest costs, such as subagents", then: "The per-model recommendations that follow override this table where they differ" `[plat-effort]#effort-levels`.

- Haiku 5.5: "Start with `medium` for most work, including agentic coding. Use `low` ... for chat, short tool tasks, and simple, high-volume requests. In long agent prompts, the model is more likely to skip a search, stop early, or skip a check at `low`. Use `high` for knowledge work, longer agent tasks, and strict instruction following. Use `xhigh` or `max` only where your evals show a quality gain, and compare them with Claude Sonnet 5.5" `[plat-effort]#recommended-effort-levels`. Also: "At `low` and `medium` effort, Claude Haiku 5.5 sometimes reports a code change as done without running a check", and "At `xhigh` effort in multi-turn chats, the model sometimes writes its whole answer in its thinking and ends the turn with no visible text" `[prompt-haiku]`.
- Sonnet 5.5: "Start with `high` unless your workload is agentic or latency-sensitive. For agentic coding and multistep tool use, start with `medium` for well-specified tasks and move to `high` for harder or longer ones" `[plat-effort]#recommended-effort-levels`. And: "At `low` and `medium`, on long agentic tasks, it's more likely to stop and check in with the user before it completes its work" `[prompt-sonnet]#calibrate-effort`.
- Opus 5.5: "Start at `medium`, the default ... and test several levels against your own evals"; "Reserve `xhigh` and `max` for work where you've measured a quality gain" `[prompt-opus]#calibrate-effort`.
- Fable 5.2: "Start with `high`. Most of the gain in capability is already present at `high`, and above it, gains are uneven and depend on the task. Step up to `xhigh` for your hardest long-running coding and agentic work, where you've measured a gain" `[plat-effort]#recommended-effort-levels`. "A turn can end before the work is done ... This occurs more often at `low` and `medium` effort" `[new-fable]`. Yet `low` "suits ... high-volume workloads (intelligent and fast subagents, for example)" `[prompt-fable]#calibrate-effort`.
- Claude Code's own table: `medium` "fits day-to-day engineering work with a clear scope, such as implementing a new feature"; `high` is for "Work where verification matters or edge cases are likely, such as fixing a bug in an existing codebase"; `low` for "Quick exchanges where you review each result"; `max` for "Hard problems you want Claude to work through without you, such as finding security vulnerabilities", which "may show diminishing returns and is prone to overthinking" `[model-config]#choose-an-effort-level`.
- `xhigh` in the API table: "Long-running agentic and coding tasks (over 30 minutes) with token budgets in the millions" `[plat-effort]#effort-levels`.

### Effort for subagents

- "On another model, that level doesn't apply. To pick a subagent's level, set `effort` in its definition" `[model-config]#effort-level-of-subagents-and-teammates`. Since 2.1.317 such a subagent uses "that model's saved effort level or its default, instead of the session's effort level" `[changelog]` 2.1.317. So without the router a routed Fable or Sonnet subagent runs at Claude Code's `medium`, below the API default `high` the per-model advice starts from.
- Per call, effort is set "When you ask Claude to run a non-fork subagent at a specific effort level" `[sub-agents]#choose-an-effort-level`.
- Model guidance for subagents: "Haiku is a good fit for subagents doing simple jobs" `[best-practices]#change-the-model-and-effort`; "For simple subagent tasks, specify `model: haiku`" `[costs]`. Haiku subagents under Opus at `medium` on coding cut cost per attempt 32% and the pass rate about 4 points, "about what a mix of `low` and `medium` without subagents would give at that cost" `[plat-optimizing]`, the section on delegating bulk work.

### Model against effort, and how to climb

- "Tuning effort is often a better lever than switching models" `[plat-choosing]#establish-key-criteria`. "Sweep effort on your own workload before you add a second model" `[plat-optimizing]#tune-effort`.
- "Quality isn't good enough: If you lowered effort, restore it; otherwise try the next tier up at its default effort" `[plat-optimizing]#start-here`.
- Effort can stand in for a step of model on some work: Opus at `low` matched Sonnet at `high` "inside run-to-run noise, for about 7% more per solved task" `[plat-optimizing]#compare-models-on-cost-per-task`; "On several evaluations, Sonnet 5.5 at Max effort even performs comparably to Opus 5.5" `[news-sonnet]`. The other way: Fable 5.2 "at High effort scored about level with Opus 5.5 at Max effort, for roughly half the cost per task" on FrontierCode 2.0 `[news-fable]`.
- The cheapest measured policy is a climb on a failure signal: `low` with failures re-run at `high` solved about 97% for about $0.17, against 95.3% for $0.29 with every task at `high`; "you need a failure signal ...; a checker that passes bad work lets those failures through" `[plat-optimizing]#re-run-failures-at-higher-effort`.

### How many levels

No page names levels of work or a count of them. The docs give four models, five efforts, a start point per model and an order to climb in: restore effort, then the next model at its default, with Fable after Opus at `xhigh` or `max` falls short `[plat-optimizing]#start-here`, `[plat-choosing]`.

## Verdict

Partly right.

The order of the rungs and three of the five settings match the docs. `lookup` on Haiku at `medium` is Haiku's documented start. `complex` on Opus at `high` is Claude Code's setting for bug fixes. `frontier` on Fable at `high` is Fable's documented start, which matters because Claude Code would otherwise run Fable at `medium`. Opus `xhigh` before Fable is the documented climb order, and Opus `xhigh` matched Fable `high` for about half the cost `[plat-optimizing]`.

Four things don't hold up:

- `standard` runs Sonnet at `medium`. The docs allow that only for well-specified work, and the router can't tell whether a brief is well-specified. It measured about 7 points below `high`, and on long runs it tends to stop and check in with a user that a subagent doesn't have.
- `hardest` starts at Opus `xhigh`, which the docs reserve for measured gains (2.5 times the cost of `high` for 1.4 points in a single run).
- `priority: quality` moves up a model before it moves up effort. That jumps `complex` past Opus `xhigh` straight to Fable, against "Tuning effort is often a better lever than switching models".
- The `cost` and `speed` ranks change no pick on the shipped file and stand for per-token price. The docs say to compare cost per completed task, not per token.

## What to change

None of these is required by a source: no shipped setting contradicts a documented fact. Every item is judgment, backed by the sources named.

1. **ladder-1. Run `standard` at `high`** (small, judgment). Change `levels.standard.effort` from `medium` to `high` in `plugins/model-router/config/models.json`. Update `test_the_shipped_ladder_picks_at_balanced_priority` and the related pick tests in `tests/test_model_router.py`, the `$comment` in `models.json`, and the "Lookup and standard run at medium" row in `docs/CLAUDE_CODE_DOCS.md`. Nothing is deleted. Backed by `[plat-effort]#recommended-effort-levels` ("move to `high` for harder or longer ones"), `[plat-optimizing]#tune-effort` (about 7 points, 62.7% against 77.8%) and `[prompt-sonnet]#calibrate-effort` (stops to check in at `medium`). Cost: about 1.7 times per Sonnet task (the inverse of "about 59%"). That still lands near Opus `low` per solved task. A smaller option is to leave `medium` and accept the measured loss. A larger one is `standard` on Opus at `medium`, the docs' main start point, at about twice Sonnet `high`'s cost per solved task.
2. **ladder-2. Have `priority: quality` raise effort before the model** (small, judgment). In `registry.Registry.select` and `ladder.pick`, `quality` should keep the balanced model and take one effort step up, at most `xhigh`. Only a level already at `xhigh` moves up a model at that model's start effort. With the efforts of 2026-11-24 the picks would then be haiku high, sonnet high, opus xhigh, fable high, fable xhigh. After ladder-1, `standard` under `quality` would be sonnet xhigh, so it may be better to give it opus medium explicitly, which ladder-3 makes easy. This deletes the `step(needs, 1)` branch in `select`. Update `test_a_pick_at_or_above_the_levels_need_gets_no_extra_step` (it asserts complex under quality is fable high). Backed by `[plat-choosing]#establish-key-criteria`, `[plat-optimizing]#start-here` ("If you lowered effort, restore it; otherwise try the next tier up at its default effort") and `[plat-choosing]` (Fable after Opus at `xhigh` or `max` falls short).
3. **ladder-3. Replace the strength, cost and speed ranks with a fixed table** (medium, judgment). Write each level's model and effort per priority into `models.json` as a short ordered list, for example `standard: [["sonnet","high"], ["opus","medium"]]`. The first usable entry wins. A user's or project's file overrides the list, not ranks. This deletes `Model.cost` and `Model.speed`, `Registry.select`'s step, cost and speed code, and `LevelSpec.needs` as a number. `Registry.strength` stays only if `clamp`, the cap and the redo's "next model up" still need an order, which the list order can give. It also changes the override-file shape, so `CONFIG_SCHEMA` goes up with a reader for schema 1 files, and `docs/ARCHITECTURE.md` "Compatibility" changes. On the shipped file every pick stays the same; the change only removes machinery that never decides anything. Backed by `[plat-optimizing]#compare-models-on-cost-per-task` ("Compare models on cost per completed task, not per token", which a per-token rank can't express) and `[best-practices]#know-what-subagents-cost`, which describes a fixed mapping in definitions. The smaller option is to drop only `speed` and `cost` and keep `needs`.
4. **ladder-4. Make `hardest` a climb rung once a real failure signal exists** (medium, judgment, depends on the redo work in `docs/research/caller-levels-and-redo.md`). A first call profiled or named `hardest` would start at Opus `high`, and only a failed check moves it to `xhigh`. In `router.decide` and `profile.profile_call`, the first pick for `hardest` becomes the `complex` pick, and the climb in `router.decide` is unchanged. Nothing is deleted. Until a failure signal fires in real use, keep `hardest` as a first pick: without one, starting lower costs quality, and quality ranks first. Backed by `[prompt-opus]#calibrate-effort` ("Reserve `xhigh` and `max` for work where you've measured a quality gain"), `[plat-optimizing]#tune-effort` ($0.74 against $0.29 for 1.4 points in a single run) and `[plat-optimizing]#re-run-failures-at-higher-effort` (the climb needs a failure signal).
5. **ladder-5. Hold Haiku's extra effort step at `high`** (small, judgment). In `ladder.effort_on`, when the pick is Haiku, stop the extra step at `high`, not `xhigh`. Update `test_a_pick_weaker_than_the_levels_need_gets_one_more_effort_step` (it asserts complex on Haiku alone runs at `xhigh`). Nothing is deleted. Backed by `[plat-effort]#recommended-effort-levels` (Haiku `xhigh` "only where your evals show a quality gain, and compare them with Claude Sonnet 5.5") and `[prompt-haiku]#use-effort-to-control-thinking` (an empty visible reply at `xhigh` in multi-turn chats).
6. **ladder-6. Run `docs-researcher` at Haiku `high`** (small, judgment). Set `effort: high` in `plugins/model-router/agents/docs-researcher.md` and keep `scout` and `verifier` at `medium`. Reading and summing up docs is knowledge work. Update any test that pins the crew's effort to `medium` for this agent. Nothing is deleted. Backed by `[plat-effort]#recommended-effort-levels` (Haiku "Use `high` for knowledge work, longer agent tasks, and strict instruction following"). Haiku at `high` still costs a small fraction of Sonnet at `medium` per token `[plat-models]`.

## What the sources do not say

- How many levels of work a router should have, or any taxonomy of work kinds. Not documented. The five names are this project's own.
- How many effort steps make up for one model step. Not documented, and the effort scale "is calibrated per model" `[model-config]`, so `ladder.effort_on`'s one step is a guess in the right direction. Two points of evidence exist: Opus `low` against Sonnet `high`, and Sonnet `max` against Opus on "several evaluations".
- Any measurement of Sonnet 5.5 `medium` against `high` on short, well-specified tasks. The 7-point figure is SWE-bench Pro. Not documented.
- Haiku 5.5's loss at `medium` against `high`, or at `low` against `medium`, on search, check or knowledge tasks. Not documented. The repo's own bench (`docs/BENCHMARKS.md`, a local measurement, not a source) had Haiku at its default solve 6 of 10 security-review runs and Haiku at `high` 10 of 10. That hints a Haiku `high` rung might beat Sonnet `medium` on cost for read-only work, but 10 runs can't show it.
- Whether Fable 5.2 helps on research. The docs disagree. `[plat-choosing]` lists "multistep deep research" as Fable work, while `[plat-optimizing]` measured no gain on DeepResearch Bench II for 2.9 times Opus's cost. `models.json` `fable.use_for` says "long investigations"; no change is backed until the docs agree.
- What Claude Code does when a subagent call carries an `effort` for a model that doesn't support effort, such as `haiku` resolving to Haiku 4.5 on Bedrock. Only the fallback rule for unsupported levels on supported models is documented.
- Whether `max` ever pays for a subagent. Claude Code names "finding security vulnerabilities" for it; no page gives a subagent number. The ladder never sends `max`, which is consistent with the cautions but not backed by a measurement.
- Whether the measured numbers were taken under Claude Code's system prompt or the bare API. `[plat-optimizing]` says "priced as a customer is billed" and names no harness. Not documented.
- Cost per task for non-coding work at each effort, apart from DRACO, HLE, the physics set and DeepResearch Bench II. For document, data and writing work the ladder's efforts rest on coding numbers and on the per-model "start here" advice.
