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

- **Cold:** no bytecode cache and no state. This is the first hook after installing or updating the plugin.
- **Warm:** bytecode in place, after one seeded session (start, prompt, agent call, result, edit), with a 1 MB transcript.

Some hooks still take a quick path on most warm runs. `stop` asks for a check once and then lets go, and `agent-stopped` finds no background agent waiting.

Times are wall time per call in milliseconds, over 30 runs.

| Command | Event | Cold p50 | Cold p95 | Warm p50 | Warm p95 |
| :- | :- | -: | -: | -: | -: |
| `session-start` | SessionStart | 279 | 325 | 91 | 105 |
| `user-prompt` | UserPromptSubmit | 286 | 318 | 96 | 127 |
| `pre-agent` | PreToolUse (Agent) | 295 | 367 | 99 | 111 |
| `post-agent` | PostToolUse (Agent) | 286 | 339 | 91 | 109 |
| `post-edit` | PostToolUse (Edit, Write) | 280 | 297 | 89 | 107 |
| `agent-failed` | PostToolUseFailure (Agent) | 270 | 318 | 90 | 99 |
| `agent-stopped` | SubagentStop | 269 | 304 | 89 | 117 |
| `stop` | Stop | 290 | 369 | 97 | 111 |
| `model-switch` | PostModelSwitch | 281 | 322 | 96 | 135 |

`refresh-cache` isn't timed: it is async, so nothing waits for it, and it would fetch over the network.

The launcher's own work with no router code after it takes 48 ms (p50) here. That is `sh`, the version probe (one interpreter start) and a second interpreter start, and it is the floor under every hook. The router's own imports and work add about 40 to 45 ms on top.

Every hook stays far under its timeout in `hooks/hooks.json`: 5 seconds for `user-prompt`, 10 for the rest. Warm p95 moves by 10 to 40 ms from one run to the next on this machine, so treat the p95 column as a range.

## Mod path

The mod (`hooks/router.ts`) asks the runtime through the same launcher, so each question costs one launcher run. The mod waits for each answer:

| What | When the mod waits for it | Cold p50 | Cold p95 | Warm p50 | Warm p95 |
| :- | :- | -: | -: | -: | -: |
| `mod-start` | `session.start`, once per session | 279 | 311 | 92 | 114 |
| `mod-spawn` | `agent.spawn`, before each agent starts | 275 | 301 | 91 | 95 |
| `mod-turn` | `turn.start`, before each main-loop prompt runs | 267 | 284 | 93 | 117 |
| `mod-usage` | `turn.complete`, after the turn, before the handler returns | 263 | 282 | 89 | 120 |

Inside those, the decision itself is small. Loading the config, gathering the facts and running `router.decide` take 0.25 ms (p50) and 0.29 ms (p95) in process.

The mod's own handlers were also timed in Node with the runtime's answer returned at once. `agent.spawn`, `turn.start` and `turn.step` take 0.001 to 0.002 ms (p50) and at most 0.003 ms (p95). Almost all of the mod path's time is starting Python.

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
| `edits.json` | 3,120 | yes | 200 sessions |
| `sessions.json` | 2,911 | yes | 200 sessions |
| `mod.json` | 1,738 | yes | 200 sessions |
| `launched.json` | 1,614 | yes | 200 sessions |
| `pending.json` | 812 | yes | 200 sessions |
| `state.json` | 685 | yes | One record of options |
| `learned.json` | 144 | yes | One entry per level |

"Bounded" was checked, not read from the code. The script lowers the three caps and runs past them twice, once with many short sessions and once with one long session. It records each file's largest size during each round. A file counts as bounded when its peak in the second round, which runs twice as many calls, is at most 25% (or 2 KB) above its peak in the first.
