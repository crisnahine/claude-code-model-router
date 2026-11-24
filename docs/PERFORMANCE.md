<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="research/architecture-review.md">Architecture review</a> &nbsp;&middot;&nbsp; Next: <a href="SECURITY.md">Security</a></p>
<!-- End of page head. -->

# Performance

What the router costs in time and disk, measured with no paid calls on one machine. Yours will differ. To reproduce:

```bash
python3 bench/overhead.py --runs 30 --calls 1000        # tables
python3 bench/overhead.py --runs 30 --calls 1000 --json # the same as JSON
```

It uses throwaway folders, so it never touches your plugin state.

Measured on 2026-11-25 on the code at `0c700b9`: macOS 27.2.1 on arm64 (11 cores), Node 22.22.3, Claude Code 2.1.331, with other work running on the machine (load average about 4). The hooks ran twice: once with Apple's Command Line Tools Python 3.9.6 as `python3` (`--runs 30 --calls 1000`), and once with Python 3.13.13 first on `PATH` (`--runs 30 --calls 200`). The script needs no `claude` CLI: it only asks `claude --version` for the machine line.

## Settings hooks

Each hook runs as `hooks/hooks.json` runs it: `sh scripts/model-router.sh <command>` with the hook's JSON on stdin ([hook input](https://code.claude.com/docs/en/hooks.md)), a different brief each run so repeats don't read as refused rewrites. Warm runs after one seeded session, with a 1 MB transcript. The script means cold to start with an empty bytecode cache, set through `PYTHONPYCACHEPREFIX`, but the launcher runs Python with `-I`, which ignores that variable. So the plugin's bytecode, where Python writes any, sits in its own `scripts/modelrouter/__pycache__`, the standard library loads its installed bytecode, and cold differs from warm only in the state. Cold came within 9 ms of warm at p50 on every command, so the tables show warm.

Apple's Python 3.9.6 (`/usr/bin/python3`) writes no bytecode under `-E` or `-I`: `sys.dont_write_bytecode` is true there, and false on Python 3.13. So with it every hook compiles the plugin's modules again.

Wall milliseconds per call, warm, over 30 runs. `stop` (asks for a check once) and `agent-stopped` (finds no background agent waiting) take a quick path on most runs.

| Command | Event | 3.9.6 p50 | 3.9.6 p95 | 3.13 p50 | 3.13 p95 |
| :- | :- | -: | -: | -: | -: |
| `session-start` | SessionStart | 164 | 193 | 48 | 51 |
| `user-prompt` | UserPromptSubmit | 171 | 197 | 46 | 52 |
| `pre-agent` | PreToolUse (Agent) | 171 | 201 | 50 | 62 |
| `post-agent` | PostToolUse (Agent) | 167 | 219 | 47 | 58 |
| `post-edit` | PostToolUse (Edit, Write, NotebookEdit) | 165 | 217 | 45 | 46 |
| `agent-failed` | PostToolUseFailure (Agent) | 166 | 211 | 45 | 56 |
| `agent-stopped` | SubagentStop | 167 | 197 | 46 | 50 |
| `stop` | Stop | 167 | 204 | 47 | 50 |
| `model-switch` | PostModelSwitch | 171 | 203 | 47 | 54 |

Every hook stays far under its `hooks.json` timeout (5 seconds for `user-prompt`, 10 for the other timed ones). Warm p95 moves by 10 to 40 ms between runs, so read it as a range. Starting `sh` and `python3 -c pass`, with no router code, takes about 24 ms (p50) with either Python, the floor under every hook. The script starts that floor without the launcher's `-I`, so it is only close to how a hook starts. Above it, each hook took about 140 to 150 ms more on Apple's 3.9.6 and 21 to 26 ms more on 3.13.

### What changed the numbers

On 2026-11-22, before the launcher ran Python with `-I`, warm hooks on the same Apple Python 3.9.6 took 54 to 60 ms (p50), with bytecode in place. Under `-I` that Python writes none, so each hook now compiles the plugin's modules again. No stored run splits that cost out from the rest. Before that, two changes had cut warm hooks from 88 to 94 ms. `model-router.py` checks its own version and exits 87 when it is too old, so a working run starts Python once (only a failed run pays for a version probe). And `urllib.request` and `xml.etree`, about 20 ms of imports, left the hook path; the plugin imports neither now, and `test_hooks_dont_load_the_network_or_xml_modules` fails if the hook path does.

## Mod path

The mod (`hooks/router.ts`) asks the runtime through the same launcher, one run per question, and waits for the answer:

| What | When the mod waits for it | 3.9.6 p50 | 3.9.6 p95 | 3.13 p50 | 3.13 p95 |
| :- | :- | -: | -: | -: | -: |
| `mod-start` | `session.start`, once per process | 176 | 225 | 48 | 54 |
| `mod-spawn` | `agent.spawn`, before each agent starts | 174 | 222 | 49 | 70 |
| `mod-usage` | `turn.complete` of a subagent's turn, after the turn, before the handler returns | 168 | 196 | 46 | 49 |

In process, loading the config, gathering the facts and running `router.decide` take 1.11 ms (p50) and 1.32 ms (p95) on 3.9.6, and 0.98 and 1.15 ms on 3.13. The mod's own handlers, timed in Node with the runtime's answer returned at once, take 0.05 ms (p50) and 0.07 ms (p95) for `agent.spawn`, and at most 0.001 ms (p50 and p95) for `turn.start` and `turn.step`, whose slowest sample was 0.06 ms, so almost all of the time is starting Python. Not timed: before `mod-start` and `mod-spawn` the mod reads the five settings sources, waiting at most 2 seconds (`SETTINGS_MS` in `hooks/router.ts`) and not again for a source that took longer, and while its runtime answers its `tool.call` hook writes one empty mark file per `Agent` call. `turn.start` and `turn.step` start no Python.

### Per request

Added up at warm p50 (3.9.6, then 3.13), since each of these waits for its answer:

| What | Commands it runs | 3.9.6 | 3.13 |
| :- | :- | -: | -: |
| A prompt | `user-prompt`, `stop` | 338 | 93 |
| An `Agent` call on the mod path, one subagent turn | `pre-agent`, `mod-spawn`, `post-agent`, `agent-stopped`, `mod-usage` | 848 | 239 |
| An `Agent` call on the fallback path | `pre-agent`, `post-agent`, `agent-stopped` | 505 | 144 |
| An edit | `post-edit` | 165 | 45 |

Each more subagent turn on the mod path adds one `mod-usage`. On the mod path `pre-agent` only takes the mod's mark and returns, but `hooks.json` can't skip it there, and it routes the call when no mod does.

## Context the plugin adds

Measured 2026-11-23 on Claude Code 2.1.330, on the older design that still read the brief, with a headless Sonnet run that only says "ok" (`--setting-sources project`, a plugins root of its own): the first request's prompt (input plus cache read and write) was 19,286 tokens without the plugin, 20,013 with it before the latest cut, and 19,969 after. The plugin then added 683 tokens, down from 728; an earlier cut, measured on Haiku, took it from 1,215 to 728. Each source was measured with that source removed (no `agents/` folder, no `skills/` folder, or no session-start hook).

| Source | When it's paid | Before | After |
| :- | :- | -: | -: |
| Session-start note (`hooks.session_start`) | At session start, resume, clear, compact and fork | 147 tokens | 128 |
| The role agents' names, descriptions and tools in the agent list (seven when measured, five now, not measured again) | Once per session | 493 | 493 |
| The `route` skill's listing (`models` and `usage` set `disable-model-invocation`, so theirs aren't in context, https://code.claude.com/docs/en/skills.md#control-who-invokes-a-skill) | Once per session | 84 | 61 |
| The note that the request names a model | Per prompt, only when the request names one | about 34 | about 34 |

The first three are measured, the last is rendered text at about 4 characters a token: today's note is 135 characters. On the fallback path each routed spawn also gets a one-line note (`fallback._note`). Three more notes come only when something happens, none of them measured: the Stop check's ask, 511 characters (about 128 tokens) each time it asks (`gates.unverified_edit`); a one-sentence note after an `Agent` call fails on a model that looks unavailable or a full ID the provider refused (`hooks.agent_failed`); and a one-sentence note when a subagent switched models mid-run (`hooks.post_agent`). None of the first three was measured again on the current design: the session-start note, the agents' descriptions and the `route` skill's description have all changed since.

Since that measurement the session-start note carries the table of which model and effort fit which kind of work, because the Agent tool sets `effort` only when instructions ask for it. It is 687 characters on a session with no pinned model, about 170 tokens at 4 characters a token, not measured, and a test holds it to 700 in the default, `CLAUDE_CODE_SUBAGENT_MODEL` and FORCE setups. The notices meant for you, such as a pinned subagent model or a pinned workflow model, go to a message shown to you when a session opens (`systemMessage`, https://code.claude.com/docs/en/hooks.md#json-output), so they add nothing to Claude's context.

A once-per-session source is cached on the first request and read from the cache on later ones, until it lapses or the conversation is compacted (https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md).

The cost benchmark's stored rows measured an older design. On the six harder pilot tasks that router cost more than no router. The redesigned plugin has not been benchmarked yet, so this page claims no saving and no quality figure.

## State locks a spawn takes

Every read-modify-write of a state file takes that file's lock ([State](ARCHITECTURE.md#state)), which is one more wait for agents started in one message and one more chance for a skipped write. `bench/overhead.py` counts them in the 1,000-call simulation (`locks` in `--json`), rerun 2026-11-25 on the machine above:

| Path | Locks per spawn before | After | Files after |
| :- | -: | -: | :- |
| Mod (`mod-spawn`) | 3.0 | 2.0 | `ledger.jsonl`, `routes.json` |
| Fallback (`PreToolUse`) | 4.0 | 3.0 | the same, and `pending.json` for the rewrite waiting for its result |

Before is the code that also wrote each spawn's count and votes to `learned.json`, which this release no longer reads or writes. Now every spawn takes one lock per file it writes, which a test counts on a single spawn too (`SpawnPaths.test_a_spawn_takes_one_lock_per_state_file_it_writes`). Folding `pending.json` into the route would take the fallback to two, and waits until `locks.json` shows it matters.

## State size

1,000 routed calls were simulated with the real hook functions, 20 per session, even sessions on the fallback path and odd ones on the mod path, each mod session sending its own settings layers. Each call names a model and has its own description, as Claude makes them. One call in four is a background agent finished by `SubagentStop`, every call adds a usage row, every fifth an edit and every fiftieth a model error. Rerun 2026-11-25:

| File | Bytes after 1,000 calls | Bounded | What bounds it |
| :- | -: | :- | :- |
| `ledger.jsonl` | 800,298 | yes | Rows older than 30 days go once the oldest one is (`store.LEDGER_MAX_DAYS`), and it is halved when it passes 4 MiB (`store.LEDGER_MAX_BYTES`), so it reaches the size cap after about 5,000 calls |
| `routes.json` | 185,882 | yes | Sessions written in the last day (`calls.KEPT_FOR`), at most the 200 most recently written (`store.KEEP_ENTRIES`), and 100 spawns per session (`store.KEPT_RECORDS`) |
| `done.json` | 97,262 | yes | Same as `routes.json` |
| `allowlist.json` | 5,501 | yes | The 200 most recently written sessions |
| `edits.json` | 3,100 | yes | Same |
| `sessions.json` | 2,904 | yes | Same |
| `launched.json` | 1,599 | yes | Same as `routes.json` |
| `pending.json` | 793 | yes | The 200 most recently written sessions, 100 calls each |
| `state.json` | 503 | yes | One health entry per model, and per full ID the mod sent that failed |

Each state file also has a `<name>.flock` lock file beside it, which holds at most a session id and which the table leaves out. The simulation calls the runtime directly, so it writes no `mod-calls/` marks. Each mark is an empty file that the call's `PreToolUse` hook removes, and `mod-start` clears any left for over a day.

`locks.json` holds one entry per state file; the simulation doesn't write it, since one process never waits on a lock.

After an upgrade from 0.1.2, files this release never reads, writes or removes stay in the data folder until you delete them, and the table leaves them out: `learned.json`, `upstream.json`, `mod.json`, `prompts.json` and the `<file>.json.lock` lock files. 0.1.2's ledger rows, which also carry a 120-character `description` and the `signals` its scorer matched, stay in `ledger.jsonl` until they pass 30 days or the 4 MiB halving drops them.

The "Bounded" column comes from a run of the script. The script lowers three caps (the ledger to 16 KiB, sessions to 5, calls per session to 10) and runs past them in two rounds, the second with twice the calls, once with one call per session (`max(40, calls // 5)` sessions, then twice as many: 200, then 400 at `--calls 1000`) and once with one long session (`max(50, calls // 5)` calls, then twice as many). A file is bounded when its second-round peak is at most 25% plus 256 bytes above its first. Whether the check reports a file as unbounded once a trim is removed hasn't been tried on this code's file set, and no output of an earlier try is stored. `OverheadBench.test_the_overhead_bench_reports_every_hook_and_state_file` holds `ledger.jsonl` and `state.json` to bounded at `--calls 40` on every change.
