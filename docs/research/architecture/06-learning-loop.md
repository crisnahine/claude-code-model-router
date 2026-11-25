# The learning loop: votes that raise a level

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.3.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

Checked 2026-11-24 against Claude Code 2.1.331 (`claude --version`), with every page fetched the same day. Code read in the `redo-fixes` worktree.

## What we built

- `plugins/model-router/scripts/modelrouter/learn.py` keeps one tally per level in `learned.json` under `store.data_dir()`, which is `${CLAUDE_PLUGIN_DATA}`: one file for the machine, shared by every project and every kind of work.
- `learn.spawned` (called from `spawn._record`) adds `calls: 1` only for a subagent whose model the router chose (`learn.counted`). It casts votes three ways: a router redo votes `up: 1` on the level it climbed from (`escalated_from`, only when a finished run is on record); a call where Claude names a model stronger than the pick votes `up: 1`; the same call named stronger after a weaker routed run in the same request votes `up: 2`. Naming a weaker model while the level is raised votes `relax: 1`. A model the user named in the prompt casts nothing.
- `learn._tally` raises a level one step (`MAX_BUMP = 1`) once `votes >= max(6, 0.3 * calls)`, then zeroes the tally. The same bar on `relax` undoes it. Nothing decays with time.
- `learn.raises` feeds `ladder.pick` through `settings.Snapshot.raises`, `router.Facts.raises`, `request.start` / `request.raise_to` (the prompt note and the strong-agent Stop gate) and `report.policy`. `learn.swapped` takes back a count when `modelsUsed` shows a mid-run swap.
- A raise is silent. The user sees it only in `/model-router:models` ("raised by feedback", and a `levels` table), and `models --reset` (`cli._reset_feedback`, `learn.reset`) clears all levels at once, together with model health. There is no option to turn learning off.
- The docstring is plain about the signal: "The votes are counted from outcomes; answer quality itself is never measured."

## What the sources say

**No Claude Code page describes a plugin that changes its own routing from what it observes.** Searching the full docs text (https://code.claude.com/docs/llms-full.txt) for this finds only Claude's own auto memory. The per-call choosers the docs name are the definition's `model` and the per-call parameter Claude passes (https://code.claude.com/docs/en/sub-agents, "Choose a model"; see also `docs/research/caller-levels-and-redo.md` Q2). A learned, persistent change to routing is not documented.

**The plugin data folder is documented as persistent state, machine-wide, with no project in its path.**

- Changelog 2.1.78: "Added `${CLAUDE_PLUGIN_DATA}` variable for plugin persistent state that survives plugin updates; `/plugin uninstall` prompts before deleting it" (https://code.claude.com/docs/en/changelog).
- "`${CLAUDE_PLUGIN_DATA}`: a directory that survives updates, for `node_modules`, virtual environments, and caches. It resolves to `~/.claude/plugins/data/<id>/`" (https://code.claude.com/docs/en/plugins/components, "Reference plugin paths and store data"). The manifest reference lists its use as "Installed dependencies such as `node_modules`, generated code, and caches" (https://code.claude.com/docs/en/plugins/manifest-reference, "Environment variables").
- "By default, Claude Code deletes the `${CLAUDE_PLUGIN_DATA}` directory when you uninstall the plugin from the last place it's installed" (same page). Uninstall is the only reset the docs give for it.
- A mod's `$.store` is "A key-value store that every session on the machine shares" (https://code.claude.com/docs/en/plugins/mods/reference), for "Settings, history, anything the user expects to find next time" (https://code.claude.com/docs/en/plugins/mods/interface, "Keep state").

So keeping state there is allowed. Nothing says a plugin should use it to change what later sessions do, or how the user should see or reset that.

**Where Claude Code itself learns across sessions, it is scoped, visible, editable and can be turned off.**

- Auto memory: "Auto memory lets Claude learn from your corrections without manual effort." Its scope is "Per repository, shared across worktrees" (https://code.claude.com/docs/en/memory, "CLAUDE.md vs auto memory").
- Seen: "When you see messages like "Saved 2 memories" or "Recalled 2 memories" in the Claude Code interface, Claude is actively updating or reading" memory. Edited: "Auto memory files are plain markdown you can edit or delete at any time." Turned off: the `/memory` toggle, `autoMemoryEnabled: false` in one project's settings, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` (same page, "Auto memory").
- Subagent memory picks a scope, `user`, `project` or `local`, and "`project` is the recommended default scope" (https://code.claude.com/docs/en/sub-agents, "Enable persistent memory").
- The one learned state that changes model choice, on Bedrock and Agent Platform: "Claude Code can remember the refusal on this machine for up to a day ... To turn the memory off, set `CLAUDE_CODE_SKIP_MODEL_ACCESS_MEMORY=1`" (https://code.claude.com/docs/en/amazon-bedrock, "Remembered model refusals"). It has a time limit, a recheck and an off switch.

`learned.json` has none of these: it spans every project, decays never, says nothing when it raises a level, can't be turned off, and resets only along with model health.

**Anthropic's documented way to change a model or effort setting from observed results is measured, with outcome checks and a noise bar.**

- "Step down to `medium` or `low` where your evals show quality holds"; "Use `xhigh` or `max` only where your evals show a quality gain" (https://platform.claude.com/docs/en/build-with-claude/effort). "adjust up or down based on your evals" (https://platform.claude.com/docs/en/about-claude/models/choosing-a-model).
- "Pull a few tasks from production logs, weighted like real traffic, and write outcome checks for each: tests pass, ticket closed, row count correct" and "Run the winner in shadow on a traffic slice before cutover, then keep the suite running" (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence, "Measure on your own workload").
- The automated search Anthropic ships (`/claude-api hillclimb`) needs two guards: "Hold out a split the search never reads" and "Repeat the baseline before the first change. Identical runs of one starting configuration differed by several tasks in these measurements" (same page, "Automate the search with the Claude API skill").
- Anthropic's evals post lists production signals as "Signals can be noisy" and "Lacks ground truth for grading", and user feedback as "Sparse and self-selected" (https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). It also says "Because model outputs vary between runs, we run multiple trials", and that trials sharing a cause "are not independent".

**A redo that works at a higher level is weak evidence that the first level was too low.** On the re-run policy the learning is modeled on: "Use this policy for the saving, not the lift. Most of the small lift is the second attempt: re-running the failures of one `high` run at `high` scores about the same, for more money." Also: "you need a failure signal (here, the benchmark's own tests); a checker that passes bad work lets those failures through" (https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence, "Re-run failures at higher effort"). The per-call climb uses this policy. Learning from the redo then treats a second attempt as proof about every later call at that level. The page doesn't say that.

**A raise is not free quality.** "Claude Fable 5.2 cost 4.3 times as much as Claude Sonnet 5.5 for no measurable gain in score, and 2.9 times as much as Claude Opus 5.5 for a score 1.9 points lower" on DeepResearch Bench II (same page, "Compare models on cost per task"). A raised `complex` asks for Fable, and through `request.start` the strong-agent Stop gate then asks a Sonnet session for a Fable agent (test `test_the_prompt_note_and_the_stop_gate_follow_a_raised_level`). On work like research, that can cost more and score lower.

**Neither documented test path ever sees a learned raise.** In `claude plugin eval`, "Each run gets a temporary home directory, working directory, and Claude Code configuration" (https://code.claude.com/docs/en/plugin-evals, "How runs are isolated"), so `~/.claude/plugins/data/<id>/` starts empty on every run. The repo's bench does the same on purpose (`docs/BENCHMARKS.md`: "every router run starts from an empty data folder"). The loop's effect on quality or cost has never been measured.

**What the code does with the signal (read from the code, not from a source).**

- The share isn't a share. `calls` counts only router-chosen spawns, but caller-named calls cast votes without adding to `calls`. A level with 0 to 20 router calls is raised by 6 votes, and the "30%" can pass 100%.
- One task can farm it. Votes aren't limited per request, session or task. One request that fans out 6 calls naming `opus` over a `standard` pick raises `standard` for every later call in every project. So do 3 redone calls named stronger (`up: 2` each), which is how the test helper `raise_lookup` does it.
- The votes mostly measure the brief scorer. The session-start note asks Claude to name a model "when the task is harder or easier than its brief reads" (`docs/GUIDE.md`). A vote then says the regex in `profile.py` put this brief too low, and the raise lifts every brief at that level, from any project and any kind of work.
- Votes never expire, so 5 votes from months ago plus one today raise a level.
- In real use the loop has almost no data. The two `learned.json` files on this machine hold 1 to 2 calls per level and 0 votes.

## Verdict

**wrong.** The sources allow a plugin to keep persistent state. They don't describe a plugin changing its own routing from what it observes, and every documented way to change a model or effort setting from results uses outcome checks, repeated trials and a held-out check, run as a deliberate step (effort page, "Measure on your own workload", hillclimb). The loop's signal is Claude's own choices, which the docs never call outcomes. Six of them can come from one task, and the platform page says a redo's gain is mostly "the second attempt". Where Claude Code learns across sessions, the learning is per repository or bounded in time, shown to the user, editable, and can be turned off. `learned.json` is machine-wide, silent and permanent. The per-call redo climb in `router.decide` stays: it is the documented re-run policy. The cross-session raise should go.

## What to change

1. **Delete the learned raise** (judgment; backed by https://platform.claude.com/docs/en/build-with-claude/effort, https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence "Measure on your own workload" and "Re-run failures at higher effort", https://code.claude.com/docs/en/memory). Delete `scripts/modelrouter/learn.py` and `learned.json`. Remove the `raises` plumbing: `router.Facts.raises`, the `raises` argument of `ladder.pick`, `settings.Snapshot.raises`, `request._need` / `request.start` / `request.raise_to`, `spawn.facts`, and the `snap.raises` call sites in `hooks.py` and `mod.py`. Remove `learn.spawned` from `spawn._record`, `learn.counted` and `Route.counted` (`calls.py`, `spawn.py`), and `learn.swapped` from `calls.returned`. Keep `SWAPPED` and the health handling. Remove `report.py`'s `levels` block and "(raised by feedback)", `cli._reset_feedback`, and the feedback lines in `skills/models/SKILL.md` and `skills/route/SKILL.md` ("The router learns from both"). Also drop "learns from results in your setup" in `.claude-plugin/plugin.json` and the feedback text in `profile.py`, `__init__.py`, `ladder.py`, `README.md`, `docs/ARCHITECTURE.md`, `docs/GUIDE.md`, `GLOSSARY.md` ("Count"), `docs/TRACEABILITY.md` (S2.18, S4.9, S10.4, S12.1, S12.31), `docs/PERFORMANCE.md`, and `bench/run.py` / `bench/overhead.py` `STATE_FILES`. Delete the `Feedback` test class and the learned-raise cases (`test_quality_priority_and_a_learned_raise_take_complex_to_fable`, the reset case's feedback half, `test_the_redo_diagram_states_the_learning_thresholds`). Bump the `model-router.models/1` schema, since `levels` goes away. This removes one module and one state file, and one write per spawn.
2. **Show redos and let the user set the floor** (judgment, optional; backed by https://code.claude.com/docs/en/memory "Audit and edit your memory" and https://code.claude.com/docs/en/settings on scopes). In `report.py` usage, count ledger spawn rows with `escalated_from` set, per level, and say that a level that keeps coming back can be raised in the project's or user's `model-router.json`. The existing raise-only rule (`config._raises_only`) already does this. The user decides, in a file they can read, in the scope they choose. It adds no state file. The smaller choice is to skip this item.
3. **Only if the loop is kept rather than deleted** (judgment; backed by https://code.claude.com/docs/en/memory, https://code.claude.com/docs/en/amazon-bedrock "Remembered model refusals", https://code.claude.com/docs/en/hooks `systemMessage`). In `learn.py` / `spawn._record`: count at most one vote per task per request, keyed on the prompt id and `calls.task`. Count caller-named calls in `calls`, so the share is a real share. Key the tally by project (the git root, as auto memory does). Expire votes and raises after a set time. Tell the user through `systemMessage` when a level is raised. Add a `userConfig` option to turn learning off, and reset feedback apart from model health in `cli.py`. Keep a raise out of `request.start`, so it can't push the strong-agent gate to Fable. Each of these is code the delete in item 1 doesn't need.

None of these items is required by a source. No page forbids a plugin from learning. The sources only show that every documented learning path is checked, scoped and visible, and this one is none of those.

## What the sources do not say

- Whether a plugin may, or should, change its own routing from observed behavior: not documented, for or against.
- How a plugin should show or reset its own persistent state. The docs give only uninstall, which deletes `${CLAUDE_PLUGIN_DATA}` unless `--keep-data` is passed. Not documented beyond that.
- Whether `${CLAUDE_PLUGIN_DATA}` should hold behavior-changing state, beyond its listed uses ("node_modules, virtual environments, and caches"): not documented.
- How often Claude names a `model` on an Agent call on its own judgment, or whether naming a stronger model means the first result was weak: not documented.
- Whether a redo marked "Redo:" in `description` reflects a failed result rather than a changed request: not documented. See `docs/research/caller-levels-and-redo.md` Q3.
- How many outcome events a level change needs to clear run-to-run noise on a user's own work. The platform page says only that identical runs "differed by several tasks". No threshold is documented, so 6 votes and 30% have no source.
- Whether a raise learned on one kind of work (code) is right for another (writing, research): not documented. The DeepResearch numbers above show only that a stronger model can score lower on research.
