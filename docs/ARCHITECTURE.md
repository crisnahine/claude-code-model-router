# Architecture

Orchestrator maps each goal to a documented Claude Code plugin mechanism. Nothing depends on undocumented behavior, so a Claude Code update can change it only in ways the changelog and docs announce, which the upstream check watches for.

| Goal | Mechanism | Enforced or advisory |
| :- | :- | :- |
| Run each kind of work on the right model | Subagents in `agents/` with `model` and `effort` frontmatter | Enforced by Claude Code |
| Pick the agent by task complexity | `route` skill: a scoring rubric plus the live routing table injected with `` !`command` `` | Advisory: Claude follows it |
| Keep Claude aware of the routing every session | `SessionStart` hook adds a short context line | Advisory |
| Cap cost by model tier | `PreToolUse` hook on `Agent` denies spawns above `max_tier`, naming cheaper agents | Enforced |
| See what ran where | `PreToolUse` and `PostToolUse` hooks on `Agent` append to `${CLAUDE_PLUGIN_DATA}/ledger.jsonl`; `PostToolUse` records `resolvedModel` | Measured |
| Automate the development lifecycle | `ship` skill: plan, build, verify, review, bounded fix loops, commit on a branch | Advisory, with hard stops before push, merge and deploy |
| Notice Claude Code releases and docs changes | Async `SessionStart` hook fetches the changelog RSS and `llms.txt` at most daily; `refresh` skill reports impact; `upstream-watch` workflow opens an issue in this repo | Detection; changes ship as reviewed plugin releases |
| Test the plugin's behavior | `evals/` cases for `claude plugin eval`; unit tests for hook logic; `claude plugin validate --strict` in CI | Measured |

## Hook flow

```text
SessionStart ─┬─ session-start   (sync, <100 ms)  prints routing context, records max_tier
              └─ refresh-cache   (async)          refreshes upstream cache if older than 24 h

Agent tool call
  PreToolUse  ── pre-agent    resolve tier: explicit model > agent config > builtin map
                              tier > max_tier ? deny with suggestion : allow
                              append "spawn" row to ledger
  PostToolUse ── post-agent   (async) append "result" row with resolvedModel
```

Every hook handler catches its own errors and exits 0, so a broken network, a missing file or bad input never blocks a session. The only non-zero effect is a deliberate deny.

## Why the tier cap denies instead of rewriting the model

A `PreToolUse` hook can rewrite `tool_input.model` with `updatedInput`, but in auto mode Claude Code can then refuse the call because the server reviewed the original input, not the rewritten one (see "PreToolUse updatedInput in auto mode" in the hooks reference). A deny with a reason works in every permission mode, and Claude retries with the suggested agent or model.

## State

All state lives in `${CLAUDE_PLUGIN_DATA}`, which survives plugin updates:

- `state.json`: upstream cache, the last reviewed version and docs index, and the recorded `max_tier`
- `ledger.jsonl`: one row per spawn and per result

Skill commands run through the Bash tool, which doesn't receive plugin environment variables, so skills pass `--data-dir "${CLAUDE_PLUGIN_DATA}"` (substituted when the skill loads) and read the cap the last `SessionStart` recorded.

## Model lineup

Routing uses the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves to the newest model in each family for your provider. When a new model ships under an existing alias, the agents pick it up with no change here. A new family or a renamed alias shows up in the changelog, the `model-config` page is on the watched list, and the routing table and agents get updated in a release.

If your organization's `availableModels` allowlist blocks a family, Claude Code falls back as described in the subagents docs. `fable` isn't served by every gateway; set `max_tier` to `opus` if yours doesn't.
