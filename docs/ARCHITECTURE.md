# Architecture

Orchestrator maps each goal to a documented Claude Code plugin mechanism. Nothing depends on undocumented behavior, so a Claude Code update can change it only in ways the changelog and docs announce, which the upstream check watches for.

| Goal | Mechanism | Enforced or advisory |
| :- | :- | :- |
| Run each kind of work on the right model | Subagents in `agents/` with `model` and `effort` frontmatter | Enforced by Claude Code |
| Pick the agent by task complexity | `route` skill: a scoring rubric plus the live routing table injected with `` !`command` `` | Advisory: Claude follows it |
| Apply the routing whatever skill or plugin is driving | `SessionStart` and `UserPromptSubmit` hooks inject the policy as context | Advisory |
| Make our agents the default over the built-ins | `PreToolUse` hook on `Agent` refuses `Explore`, `Plan` and `general-purpose` with the agent to use instead; `routing_mode` sets once per prompt (`steer`), always (`strict`) or never | Enforced for the refused call; the user naming an agent overrides it |
| Cap cost by model tier | `PreToolUse` hook on `Agent` denies spawns above `max_tier`, naming cheaper agents | Enforced |
| See what ran where | `PreToolUse` and `PostToolUse` hooks on `Agent` append to `${CLAUDE_PLUGIN_DATA}/ledger.jsonl`; `PostToolUse` records `resolvedModel` | Measured |
| Automate the development lifecycle | `ship` skill: plan, build, verify, review, bounded fix loops, commit on a branch | Advisory, with hard stops before push, merge and deploy |
| Notice Claude Code releases and docs changes | Async `SessionStart` hook fetches the changelog RSS and `llms.txt` at most daily; `refresh` skill reports impact; `upstream-watch` workflow opens an issue in this repo | Detection; changes ship as reviewed plugin releases |
| Test the plugin's behavior | `evals/` cases for `claude plugin eval`; unit tests for hook logic; `claude plugin validate --strict` in CI | Measured |

## Hook flow

```text
SessionStart ─┬─ session-start   (sync, <100 ms)  prints routing context, records max_tier and model
              └─ refresh-cache   (async)          refreshes upstream cache if older than 24 h
UserPromptSubmit ─ user-prompt                    one-line policy reminder; notes built-in agents the
                                                  user named; resets the per-prompt redirect list
PostModelSwitch ─ model-switch                    records the session's new model

Agent tool call
  PreToolUse  ── pre-agent    resolve tier, in the documented order:
                                forced CLAUDE_CODE_SUBAGENT_MODEL
                                > the call's model parameter
                                > the agent's model (ours, a project override, or a built-in)
                                > CLAUDE_CODE_SUBAGENT_MODEL
                                > the session's model (transcript, then recorded)
                              another plugin's or the user's own agent: unknown, allowed
                              tier > max_tier ? deny with suggestion
                              else generic built-in, not named by the user in this prompt,
                                and routing_mode is steer (first time this prompt) or strict
                                ? deny, naming the orchestrator agent to use
                              else allow
                              append "spawn" row to ledger
  PostToolUse ── post-agent   (async) append "result" row with resolvedModel

Every hook runs `sh scripts/orchestrator.sh <command>`, which finds Python 3.9+ and exits 0 without it.
```

Every hook handler catches its own errors and exits 0, so a broken network, a missing file or bad input never blocks a session. The only non-zero effect is a deliberate deny.

## Why the tier cap denies instead of rewriting the model

A `PreToolUse` hook can rewrite `tool_input.model` with `updatedInput`, but in auto mode Claude Code can then refuse the call because the server reviewed the original input, not the rewritten one (see "PreToolUse updatedInput in auto mode" in the hooks reference). A deny with a reason works in every permission mode, and Claude retries with the suggested agent or model. Hooks read the cap only from the environment Claude Code sets, never from a stored value, so a cap from an earlier session can't leak into this one.

## State

All state lives in `${CLAUDE_PLUGIN_DATA}`, which survives plugin updates:

- `state.json`: upstream cache, the last reviewed version and docs index, and the recorded `max_tier`
- `ledger.jsonl`: one row per spawn and per result, trimmed to the newest half when it passes 4 MB
- `sessions.json`: the last known model of up to 200 sessions
- `prompts.json`: per session, the built-in agents the user named in the current prompt and the ones already redirected

Writes go to a per-process temp file and are renamed into place, so concurrent sessions can't leave a half-written file.

Skill commands run through the Bash tool, which doesn't receive plugin environment variables, so skills pass `--data-dir "${CLAUDE_PLUGIN_DATA}"` (substituted when the skill loads) and read the cap the last `SessionStart` recorded.

## Model lineup

Routing uses the aliases `haiku`, `sonnet`, `opus` and `fable`, which Claude Code resolves to the newest model in each family for your provider. When a new model ships under an existing alias, the agents pick it up with no change here. A new family or a renamed alias shows up in the changelog, the `model-config` page is on the watched list, and the routing table and agents get updated in a release.

If your organization's `availableModels` allowlist blocks a family, Claude Code falls back as described in the subagents docs. `fable` isn't served by every gateway; set `max_tier` to `opus` if yours doesn't.

## Why redirect by refusing, and why only the built-ins

The redirect could rewrite `subagent_type` with `updatedInput`, but that has the auto-mode problem described above, and a silent swap would hide from Claude which agent it is talking to. A refusal that names the replacement keeps Claude informed, and it re-issues the call.

Only `Explore`, `Plan` and `general-purpose` are redirected. An agent the user or another plugin defined was put there on purpose, with its own prompt and tools; replacing it would break that setup, and its model can't be known from outside. In `steer` mode a repeat is allowed, so a job none of our agents fits can never deadlock: the same call (same agent and prompt) issued again passes at once, and any call for that agent passes once three seconds have gone by since its first redirect. The three seconds exist so that every call of a parallel batch, which arrive together with different prompts, is redirected rather than only the first.

`UserPromptSubmit` also fires for machine-written turns such as background-task notifications. Those start with `<` and are ignored, so they don't reset the per-prompt state. Agents the user names stay allowed for the whole session. If saving the redirect record fails, the call is allowed for the same reason.
