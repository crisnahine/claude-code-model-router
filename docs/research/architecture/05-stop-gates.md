# The Stop gates: asking for a check or a stronger agent before a turn ends

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.3.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

Researched 2026-11-24 against Claude Code 2.1.331 (`claude --version`), with every page fetched the same day as `.md` from code.claude.com and platform.claude.com, and as HTML from anthropic.com. Code read in the `redo-fixes` worktree.

## What we built

- `hooks/hooks.json` registers one `Stop` command hook (`model-router.sh stop`) and one `PostToolUse` hook on `Edit|Write|NotebookEdit` (`post-edit`). `SubagentStop` runs `agent-stopped`.
- `hooks.stop` calls `gates.before_stop` and, when it gets a reason back, returns `{"decision": "block", "reason": ...}`.
- `gates.before_stop` does nothing when `stop_hook_active` is true, when `routing_mode` isn't `auto`, or when `gates.still_running` finds a `workflow` or `subagent` in `background_tasks`. Otherwise it tries the strong-agent gate first, then the verify gate.
- Verify gate: `gates.note_edit` stamps `edited_at` for any edited file under the project that isn't prose (`_NON_CODE`: `.md`, `.txt`, `.csv` and a few more). `gates.unverified_edit` stamps `verified_at` at once (so it asks once per batch of edits), then reads the last 1 MiB of the transcript file (`session.tool_calls`) for a Bash command that `gates.is_check` matches (`CHECK_COMMAND`: pytest, jest, tsc, ruff, eslint, `npm test`, `cargo build` and so on) after the last edit. If none ran, it asks for `model-router:verifier`, and `model-router:reviewer` for a risky change. `gates.note_agent_checks` does the same scan on a subagent's own transcript at `SubagentStop`.
- Strong-agent gate: `gates.escalation` fires once per request (`request.mark_escalation`) when the request's level, estimated from the prompt's wording at `UserPromptSubmit`, is `complex` or higher, the session model is below the level's need, and no agent of that strength ran (`calls.strongest`). It names the reviewer (after an edit), the architect, or the deep-solver (`gates.finished_check`, `gates.strong_agent`).
- Both are on by default (`settings.py` `verify_gate: bool = True`, `strong_agent_gate: bool = True`; `plugin.json` `userConfig` defaults `true`).

## What the sources say

What a blocking Stop hook is for

- "`decision`: `"block"` prevents Claude from stopping ... `reason`: Required when `decision` is `"block"`. Tells Claude why it should continue." https://code.claude.com/docs/en/hooks#stop-decision-control
- The same section names a second output for exactly this plugin's case: "Use `additionalContext` when the hook is working as designed and giving Claude guidance, such as "run the test suite before finishing". It keeps the conversation going through the same loop protections as `decision: "block"`, namely the `stop_hook_active` input and the 8-consecutive-continuation cap, but the transcript labels it `Stop hook feedback` and no hook error notification is shown." https://code.claude.com/docs/en/hooks#stop-decision-control
- Added in 2.1.163: "Stop and SubagentStop hooks can now return `hookSpecificOutput.additionalContext` to give Claude feedback and keep the turn going without being labeled a hook error." https://code.claude.com/docs/en/changelog
- Best practices puts a Stop hook on the "deterministic" side and says what it is for: "The deterministic kind is for when you want to make sure certain things *always* happen (or never happen), no matter what the model decides." And: "if you have hooks for things that are only preferences, delete them and let Claude decide." https://code.claude.com/docs/en/best-practices#deterministic-and-non-deterministic-instructions
- "a hook is for a rule with *zero* exceptions" https://code.claude.com/docs/en/best-practices#enforce-steps-with-hooks
- The documented verification Stop hook runs the project's own check and blocks only when it fails: `"command": "pnpm tsc --noEmit 1>&2 || exit 2"`, "Exit code 2 is what blocks the stop. Whatever the command prints to stderr goes back to Claude as the reason it can't finish yet." https://code.claude.com/docs/en/best-practices#enforce-the-check-with-a-stop-hook
- "`Stop` hooks fire whenever Claude finishes responding, not only at task completion." https://code.claude.com/docs/en/hooks-guide (section "Limitations")

`stop_hook_active` and the cap

- "The `stop_hook_active` field is `true` when Claude Code is already continuing as a result of a stop hook. Check this value or process the transcript to avoid blocking on a condition that will never resolve." https://code.claude.com/docs/en/hooks#stop-input
- "after stop hooks have continued the turn eight times in a row, Claude Code overrides the next block and ends the turn. The count of consecutive continuations resets each time Claude calls a tool. To raise the cap, set `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`." https://code.claude.com/docs/en/hooks#stop-input. The variable applies to Stop and SubagentStop, "(default: 8). Set to `0` to disable the cap." https://code.claude.com/docs/en/env-vars
- Hooks that match one event run in parallel: "When an event fires, its matching hooks run in parallel." https://code.claude.com/docs/en/hooks (section "How a hook resolves"). How several Stop hooks' block results combine (for example this plugin plus `/goal`) is not documented beyond that.

Background tasks

- "The `background_tasks` and `session_crons` arrays let hooks distinguish "session is done" from "session is paused waiting for background work to wake it back up"." Task `type` labels: "`shell`, `subagent`, `monitor`, `workflow`, `teammate`, `cloud session`, or `MCP task`." https://code.claude.com/docs/en/hooks#stop-input
- Claude Code's own Stop-hook feature defers on more than subagents: "If a subagent or a background shell command is still running when a turn ends, Claude Code skips the evaluation for that turn. It evaluates at the end of the next turn that finishes with no background work running." https://code.claude.com/docs/en/goal (section "Background work defers evaluation")

Headless runs

- Plugin hooks run in `-p` unless `--bare`: "Without it, `claude -p` discovers and loads them as an interactive session would"; under `--bare`, plugin hooks loaded with a flag don't run. https://code.claude.com/docs/en/headless (section "Start faster with bare mode")
- "`--no-session-persistence`: Disable session persistence so sessions are not saved to disk ... The `CLAUDE_CODE_SKIP_PROMPT_HISTORY` environment variable does the same in any mode." https://code.claude.com/docs/en/cli-reference
- The transcript is not a reliable record at Stop: "The transcript file is written asynchronously and may lag the in-memory conversation, so it may not yet include the current turn's most recent messages when a hook fires." https://code.claude.com/docs/en/hooks#common-input-fields. The row format the gate parses is not documented.
- This repo's own benchmark notes (`bench/analyze.py`) record the effect: runs with `--no-session-persistence` "saves no transcript, so the Stop check could never see a test run and asked for a verifier after every edit".
- `--max-turns` "Limit the number of agentic turns (print mode only)" and `--max-budget-usd` stops the run at a spend cap. https://code.claude.com/docs/en/cli-reference. Whether a Stop-hook continuation counts as a turn against `--max-turns` is not documented.

What a block costs

- Each block is one more model turn on the whole conversation. A past bug made it worse: "Fixed blocking Stop hooks causing the turn after a block to lose the model's reasoning from that turn and, on some models, miss the prompt cache" (2.1.259). https://code.claude.com/docs/en/changelog. The plugin needs 2.1.329, so that fix is in.
- "By default, hooks block Claude's execution until they complete." https://code.claude.com/docs/en/hooks (section "Run hooks in the background")
- For `/goal`, the doc states the extra spend: "Evaluation tokens are billed in addition to main-turn spend." https://code.claude.com/docs/en/goal. No token figure for a command-hook block is documented.
- Over-asking for verification costs money on current models: "over-specific instruction added to compensate for an older model: "verify twice," "be maximally thorough," ... A newer model follows such instructions to the letter, producing extra tool rounds and extra writing that you pay for with no gain in accuracy." https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence#audit-prompts-against-the-current-model

How Anthropic recommends verifying work

- First lever, a check Claude can run: "Claude stops when the work looks done. If it has nothing to run that tells it whether the work actually *is* done, "looks done" is all it has to go on." The options run "from Claude deciding for itself all the way to Claude Code enforcing it": in the prompt, `/goal`, a Stop hook, and "By someone who didn't write the code: ... `/code-review` reviews the diff in a fresh subagent." https://code.claude.com/docs/en/best-practices#give-claude-a-check-to-run
- "Give Claude something it can run that comes back pass or fail." The check "can be almost anything, as long as Claude can read the result: A test file, A build's exit code, A linter, A script that diffs output against a fixture, A browser screenshot next to the mockup." Same page.
- "No default can know those things about your project", listing "A check that has to pass before anything counts as done" among them. https://code.claude.com/docs/en/best-practices
- A reviewer subagent is worth it for a fresh view, not for a smarter answer: "Delegate to a subagent for a better answer: Handing the same task to a subagent doesn't make the answer any better. A fresh reviewer does help, because it hasn't seen your reasoning." https://code.claude.com/docs/en/best-practices#what-you-can-stop-doing
- Claude Code already nudges verification itself: "When a session starts with a skill named `verify` or `simplify` in place, Claude Code's commit instructions tell Claude to run it right before each commit, except for changes to docs or tests." Plugin skills don't count for this. https://code.claude.com/docs/en/skills#run-your-checks-before-each-commit
- "Agents often operate in a specific feedback loop: gather context -> take action -> verify work -> repeat." Three ways to verify: rules ("Code linting is an excellent form of rules-based feedback"), visual feedback, and "LLM as a judge ... generally not a very robust method, and can have heavy latency tradeoffs." https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk
- Cheap-first escalation needs a real failure signal: "you need a failure signal (here, the benchmark's own tests); a checker that passes bad work lets those failures through." https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence#re-run-failures-at-higher-effort
- A second, stronger model helps little unless it adds what the first lacks: "Where the executor rarely or never consulted, the pairing scored within run-to-run noise of the executor alone, or lower." Same page, section "Advisor strategy: escalate hard decisions".
- The Claude Code team's post (on claude.dev, linked from the anthropic.com Agent SDK post above): "Adding a second, more expensive, reviewer on top of the primary one is often a worse approach than tuning the primary." And on new gates: "We've set it up with off, observe, and enforce modes. Observe mode will log what would have been blocked, so that we can review any false positives." And "The people who own the code write the checks for it." https://claude.dev/blog/how-the-claude-code-team-thinks-about-verification/

Where a check belongs

- Inside a subagent: "Returning `decision: "block"` with a `reason` keeps the subagent running and delivers `reason` to the subagent as its next instruction." https://code.claude.com/docs/en/hooks#subagentstop-input
- For teammates: "`TeammateIdle`: runs when a teammate is about to go idle. Exit with code 2 to send feedback and keep the teammate working." https://code.claude.com/docs/en/agent-teams (section "Enforce quality gates with hooks")
- Edits made through Bash don't reach an `Edit|Write` hook: "Claude can also create or modify files by running shell commands. If your hook must see every file change ... add a `Stop` hook that scans the working tree once per turn." https://code.claude.com/docs/en/hooks-guide

Work that is not code

- The docs' check list already goes past tests: a fixture diff, a screenshot. https://code.claude.com/docs/en/best-practices#give-claude-a-check-to-run
- `/goal` judges any condition the conversation shows: "write the condition as something Claude's own output can demonstrate." https://code.claude.com/docs/en/goal
- Evaluator-optimizer "is particularly effective when we have clear evaluation criteria ... analogous to the iterative writing process a human writer might go through when producing a polished document", with literary translation as an example. https://www.anthropic.com/engineering/building-effective-agents
- Prompt and agent hooks can judge a Stop for any work: a prompt hook can return `"impossible": true`, and "On `Stop` and `SubagentStop`, Claude Code then lets the turn end." But "A `prompt` or `agent` hook asks *another model* to decide, so it isn't deterministic either", and "Agent hooks are experimental." https://code.claude.com/docs/en/hooks#prompt-based-hooks, https://code.claude.com/docs/en/best-practices#enforce-steps-with-hooks

## Verdict

Partly right.

The loop guard is right: the gates skip on `stop_hook_active`, ask once, and wait on background workflows and subagents, as https://code.claude.com/docs/en/hooks#stop-input asks. Asking for a check before done is what https://code.claude.com/docs/en/best-practices#give-claude-a-check-to-run recommends, and cheap-first routing needs that failure signal. Three things are wrong. Both gates return `decision: "block"`, which shows a hook error, where the hooks reference names `additionalContext` for guidance like "run the test suite before finishing". The verify gate decides from an undocumented transcript format that "may lag" and is missing under `--no-session-persistence`, so it asks after every edit there; it ignores background shell work that `/goal` waits on; and it applies a software-only test list to every non-prose file. The strong-agent gate fires on a wording estimate, not a failure signal, and the docs say a hook is for zero-exception rules and that a second model rarely helps unless it adds what the first lacks. So it should not be a default.

## What to change

1. **Return guidance, not a block.** In `hooks.stop`, return `{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": reason}}` in place of `{"decision": "block", "reason": reason}`. Deletes the `decision`/`reason` output. Tests in `tests/test_model_router.py` classes `Gates` and `GatesDefault` that read `["reason"]` change to read the new field. The loop guards (`stop_hook_active`, the cap) stay the same per the docs. Source: https://code.claude.com/docs/en/hooks#stop-decision-control, changelog 2.1.163. Required by a source. Small.
2. **Turn the strong-agent gate off by default.** `settings.py` `strong_agent_gate = False`, `plugin.json` `userConfig.strong_agent_gate.default` `false`, and the GUIDE and README option rows. Keep the `UserPromptSubmit` note in `hooks.user_prompt`, which gives the same advice before the work instead of after it, as non-deterministic guidance. If the harder bench tier shows no lift from the gate, delete `gates.escalation`, `gates.finished_check`, `request.mark_escalation`, `escalation_asked` and the option. Sources: https://code.claude.com/docs/en/best-practices#deterministic-and-non-deterministic-instructions ("delete them and let Claude decide"), https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence (failure signal; advisor results). Judgment. Small.
3. **Don't ask when the gate can't see.** In `gates.unverified_edit`, return no ask when `transcript_path` is missing or unreadable (covers `--no-session-persistence` and `CLAUDE_CODE_SKIP_PROMPT_HISTORY`), instead of reading "no check ran". Touches `gates.checks_ran_since` (return `None` for "can't tell") and its caller. Deletes nothing. Sources: https://code.claude.com/docs/en/hooks#common-input-fields ("may lag"), https://code.claude.com/docs/en/cli-reference, this repo's `bench/analyze.py` note. Judgment backed by sources. Small.
4. **Wait on all background work `/goal` waits on.** `gates.still_running` should also count `shell` and `teammate` tasks, not only `workflow` and `subagent`. Then delete the checker loop in `gates.unverified_edit` (and `gates.is_checker` if nothing else uses it): `still_running` already returns early for every background subagent, so that loop never runs from `before_stop`. Sources: https://code.claude.com/docs/en/goal (section "Background work defers evaluation"), https://code.claude.com/docs/en/hooks#stop-input. Judgment backed by sources. Small.
5. **Word the verify ask for any kind of work, and for a turn that isn't finished.** In `gates.unverified_edit`, ask for "the project's check" in the docs' terms (tests, build, linter, a script that diffs output against a fixture, a screenshot), name `model-router:verifier` for code, and let "the work isn't finished yet, or the turn is waiting on the user" end the turn in one line, as "no runnable checks" does now. Sources: https://code.claude.com/docs/en/best-practices#give-claude-a-check-to-run, https://code.claude.com/docs/en/hooks-guide ("Stop hooks fire whenever Claude finishes responding, not only at task completion"). Judgment. Small.
6. **Keep the verify gate on by default, and say why in the option text.** No code change. It stays on because quality ranks first and the cheap-first redo needs a failure signal; the docs don't say a plugin should enforce this by default, so `plugin.json` `verify_gate.description` and GUIDE should name it as the plugin's choice and say how to turn it off. Sources: https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence#re-run-failures-at-higher-effort, https://code.claude.com/docs/en/best-practices#give-claude-a-check-to-run. Judgment. Small.

Smaller option for item 3, if the transcript read is worth replacing outright: record checks at `PostToolUse` on `Bash` from `tool_input.command`, which is documented input, and drop `session.tool_calls` from the gate. That costs one more hook process per Bash call, so it is listed here, not as a change.

## What the sources do not say

- Whether a third-party plugin should enforce verification by default. The docs describe the user or the code owner setting the check.
- How results combine when several Stop hooks fire on one turn, for example this plugin's guidance plus a `/goal` evaluator or a user's own blocking hook.
- Whether a Stop continuation counts against `--max-turns` in a `-p` run, and what a script parsing the final result sees after one.
- Any token or time figure for one Stop continuation.
- Whether `Stop` fires inside an in-process teammate's session, and so whether these gates run there.
- Whether `SubagentStop` `decision: "block"` behaves the same for background subagents as for foreground ones (also open in `quality-first-routing.md`).
- The transcript row format `session.tool_calls` parses. Only `last_assistant_message` is the documented way to read the turn's end.
- Any measured quality gain from a stronger agent checking a weaker session model's answer after the fact. The only measured pairing is the advisor, which is consulted during the work.
- How a gate should judge work with no file edits at all, such as a report written only in the reply. The edit hook sees file tools only, and edits made through Bash are missed too.
