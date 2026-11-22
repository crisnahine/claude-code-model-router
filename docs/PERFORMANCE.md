# Performance

What the router costs in time and disk, measured with no paid calls. The numbers come from one machine and will differ on yours. The command below reproduces them.

```bash
python3 bench/overhead.py --runs 30 --calls 1000        # tables
python3 bench/overhead.py --runs 30 --calls 1000 --json # the same as JSON
```

The script uses throwaway data, config and project folders, so it never reads or writes your real plugin state. It turns the daily check off, so nothing goes over the network.

Measured on 2026-11-22: macOS 27.2.1 on arm64 (11 cores), Python 3.9.6, Node 22.22.3, Claude Code 2.1.330.

## Settings hooks

Each hook runs the way `hooks/hooks.json` runs it: `sh scripts/model-router.sh <command>` with the hook's JSON on stdin ([hook input](https://code.claude.com/docs/en/hooks.md)). Each run gets a different brief, so repeats don't read as redos.

- **Cold:** an empty bytecode cache and no state. The bench points the cache at a fresh folder, so even the standard library compiles from source. A real first hook after installing or updating the plugin usually finds the standard library compiled already, so it lands between the cold and warm columns.
- **Warm:** bytecode in place, after one seeded session (start, prompt, agent call, result, edit), with a 1 MB transcript.

Some hooks still take a quick path on most warm runs. `stop` asks for a check once and then lets go, and `agent-stopped` finds no background agent waiting.

Times are wall time per call in milliseconds, over 30 runs. The hook and mod tables come from a `--runs 30 --calls 200` run; `--calls` only sets how many calls the state simulation runs, and that run's bytes per 1,000 calls agree with the state table below within 1%.

| Command | Event | Cold p50 | Cold p95 | Warm p50 | Warm p95 |
| :- | :- | -: | -: | -: | -: |
| `session-start` | SessionStart | 182 | 231 | 59 | 82 |
| `user-prompt` | UserPromptSubmit | 187 | 208 | 58 | 64 |
| `pre-agent` | PreToolUse (Agent) | 188 | 208 | 60 | 63 |
| `post-agent` | PostToolUse (Agent) | 183 | 204 | 54 | 62 |
| `post-edit` | PostToolUse (Edit, Write) | 183 | 207 | 55 | 57 |
| `agent-failed` | PostToolUseFailure (Agent) | 187 | 203 | 55 | 57 |
| `agent-stopped` | SubagentStop | 185 | 203 | 54 | 66 |
| `stop` | Stop | 179 | 197 | 54 | 77 |
| `model-switch` | PostModelSwitch | 184 | 218 | 55 | 68 |

`refresh-cache` isn't timed: it is async, so nothing waits for it, and it would fetch over the network.

Starting `sh` and one interpreter, as the launcher does, with no router code after it takes 43 ms (p50) and 48 ms (p95) here. That is the floor under every hook. Before this change the floor moved between 45 and 79 ms (p50) from run to run; with the version probe gone it stays put. The router's own imports and work add about 11 to 17 ms on top.

Every hook stays far under its timeout in `hooks/hooks.json`: 5 seconds for `user-prompt`, 10 for the rest. Warm p95 moves by 10 to 40 ms from one run to the next on this machine, so treat the p95 column as a range.

### What changed the numbers

The first measurement, on the same machine, had warm hooks at 88 to 94 ms (p50); they are now 54 to 60 ms. Two changes cut about 30 to 40 ms from every warm hook:

- The launcher used to start Python once just to check its version, then again to run the router. Now `model-router.py` checks its own version and exits 87 when it is too old, so a working run starts Python once. Only a failed run pays for the version check, which tells a real error from a broken interpreter such as the Windows Store stub, and then tries the next one.
- `urllib.request` and `xml.etree`, about 20 ms of imports, now load only for the daily fetch and the changelog parser. A test fails if the hook path imports them again.

The cold column dropped by about 80 to 100 ms too (it was 264 to 276 ms at p50). Most of that drop is the bench's empty cache no longer compiling `urllib`, `http.client`, `email` and `xml` from source, so a real first hook gains less.

## Mod path

The mod (`hooks/router.ts`) asks the runtime through the same launcher, so each question costs one launcher run. The mod waits for each answer:

| What | When the mod waits for it | Cold p50 | Cold p95 | Warm p50 | Warm p95 |
| :- | :- | -: | -: | -: | -: |
| `mod-start` | `session.start`, once per session | 183 | 197 | 54 | 64 |
| `mod-spawn` | `agent.spawn`, before each agent starts | 187 | 197 | 58 | 70 |
| `mod-turn` | `turn.start`, before each main-loop prompt runs | 186 | 202 | 56 | 59 |
| `mod-usage` | `turn.complete`, after the turn, before the handler returns | 182 | 194 | 54 | 56 |

Inside those, the decision itself is small. Loading the config, gathering the facts and running `router.decide` take 0.26 ms (p50) and 0.29 ms (p95) in process.

The mod's own handlers were also timed in Node with the runtime's answer returned at once. `agent.spawn`, `turn.start` and `turn.step` take 0.001 to 0.004 ms (p50) and at most 0.005 ms (p95). Almost all of the mod path's time is starting Python.

## Context the plugin adds

The plugin's text is in the model's context, so it is paid for on every request. Measured 2026-11-22 on Claude Code 2.1.330 with a headless Haiku run that only says "ok" (`--setting-sources project`, a plugins root of its own): the first request's prompt (input plus cache read plus cache write) was 18,523 tokens without the plugin, 19,738 with it before the cut below, and 19,251 after, so the plugin adds 728 tokens where it added 1,215.

| Item | Before | After |
| :- | -: | -: |
| Session-start note (`hooks.session_start`) | about 386 tokens | about 104 |
| The seven role agents' names and descriptions in the agent list | about 385 | about 286 |
| The `route` skill's description (the other four skills set `disable-model-invocation`, so theirs aren't in context, https://code.claude.com/docs/en/skills.md#control-who-invokes-a-skill) | about 61 | about 49 |
| The hard-request note, only when the request needs more than the session's model | about 122 | about 97 |

Per-item figures are the rendered text at about 4 characters a token; the totals are measured. The note keeps what Claude must act on: don't name a model or effort, say what makes a subtask hard, and re-issue a call that fell short. The architect's, reviewer's and deep-solver's descriptions keep a line on when not to use them (about 30 tokens), since Claude delegates from descriptions (https://code.claude.com/docs/en/sub-agents.md) and those are the costliest agents.

## State size

1,000 routed calls were simulated with the real hook functions, at 20 calls per session:
- Even sessions take the fallback path (`PreToolUse` and `PostToolUse`), odd ones the mod path (`mod-spawn`).
- One call in four is a background agent finished by `SubagentStop`.
- Each call adds one turn's usage row, every fifth call an edit, and every fiftieth a model error.
- Each call reports the model it was routed to.

| File | Bytes after 1,000 calls | Bounded | What bounds it |
| :- | -: | :- | :- |
| `ledger.jsonl` | 773,494 | yes | Halved when it passes 4 MiB (`store.LEDGER_MAX_BYTES`), so it reaches the cap after about 5,000 calls |
| `prompts.json` | 178,447 | yes | The 200 most recently written sessions (`store.KEEP_ENTRIES`) |
| `done.json` | 112,775 | yes | Same, and 100 calls per session (`calls.KEPT`) |
| `edits.json` | 3,120 | yes | The 200 most recently written sessions |
| `sessions.json` | 2,911 | yes | Same |
| `mod.json` | 1,738 | yes | Same |
| `launched.json` | 1,614 | yes | Same |
| `pending.json` | 812 | yes | Same |
| `state.json` | 685 | yes | One record of options |
| `learned.json` | 144 | yes | One entry per level |

"Bounded" was checked, not read from the code. The script lowers the three caps (the ledger to 16 KiB, sessions to 5, calls per session to 10) and runs past them twice: once with one call per session, 40 sessions and then 80, so every per-session file holds far more sessions than its cap, and once with one long session. It records each file's largest size during each round. A file counts as bounded when its peak in the second round, which runs twice as many calls, is at most 25% plus 256 bytes above its peak in the first. The check was proven by removing each trim in turn: without the session cap, all seven per-session files report unbounded; without the ledger's halving, `ledger.jsonl` does; without the per-session call cap, `done.json` and `prompts.json` do.
