<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><img src="assets/banner-performance.svg" width="880" alt="Performance: What does the router cost in time and disk?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="research/caller-levels-and-redo.md">Redo research</a> &nbsp;&middot;&nbsp; Next: <a href="SECURITY.md">Security</a></p>
<!-- End of page head. -->

# Performance

What the router costs in time and disk, measured with no paid calls on one machine. Yours will differ. To reproduce:

```bash
python3 bench/overhead.py --runs 30 --calls 1000        # tables
python3 bench/overhead.py --runs 30 --calls 1000 --json # the same as JSON
```

It uses throwaway folders, so it never touches your plugin state.

Measured on 2026-11-22: macOS 27.2.1 on arm64 (11 cores), Python 3.9.6, Node 22.22.3, Claude Code 2.1.330. The `user-prompt` row and the `prompts.json` row were rerun on 2026-11-25 on Claude Code 2.1.331, after the router stopped sizing up the request and its record kept only the prompt id, and the `state.json` row the same day, after the options stopped being saved there.

## Settings hooks

Each hook runs as `hooks/hooks.json` runs it: `sh scripts/model-router.sh <command>` with the hook's JSON on stdin ([hook input](https://code.claude.com/docs/en/hooks.md)), a different brief each run so repeats don't read as redos. Cold is an empty bytecode cache and no state, so even the standard library compiles from source (a real first hook lands between the two). Warm is bytecode in place after one seeded session, with a 1 MB transcript.

Wall milliseconds per call over 30 runs, from a `--runs 30 --calls 200` run (its bytes per 1,000 calls agree with the state table below within 1%). `stop` (asks for a check once) and `agent-stopped` (finds no background agent waiting) take a quick path on most warm runs.

| Command | Event | Cold p50 | Cold p95 | Warm p50 | Warm p95 |
| :- | :- | -: | -: | -: | -: |
| `session-start` | SessionStart | 182 | 231 | 59 | 82 |
| `user-prompt` | UserPromptSubmit | 183 | 195 | 56 | 58 |
| `pre-agent` | PreToolUse (Agent) | 188 | 208 | 60 | 63 |
| `post-agent` | PostToolUse (Agent) | 183 | 204 | 54 | 62 |
| `post-edit` | PostToolUse (Edit, Write, NotebookEdit) | 183 | 207 | 55 | 57 |
| `agent-failed` | PostToolUseFailure (Agent) | 187 | 203 | 55 | 57 |
| `agent-stopped` | SubagentStop | 185 | 203 | 54 | 66 |
| `stop` | Stop | 179 | 197 | 54 | 77 |
| `model-switch` | PostModelSwitch | 184 | 218 | 55 | 68 |

Every hook stays far under its `hooks.json` timeout (5 seconds for `user-prompt`, 10 for the other timed ones). Warm p95 moves by 10 to 40 ms between runs, so read it as a range. Starting `sh` and one interpreter with no router code takes 43 ms (p50) and 48 ms (p95), the floor under every hook; the router adds about 11 to 17 ms.

### What changed the numbers

The first measurement had warm hooks at 88 to 94 ms (p50), now 54 to 60. Two changes cut 30 to 40 ms from each. `model-router.py` checks its own version and exits 87 when it is too old, so a working run starts Python once (only a failed run pays for a version probe; the floor used to drift between 45 and 79 ms at p50). And `urllib.request` and `xml.etree`, about 20 ms of imports, left the hook path; the plugin imports neither now, and a test fails if the hook path does. Cold fell 80 to 100 ms too (it was 264 to 276 ms at p50), mostly because the bench's empty cache no longer compiles `urllib`, `http.client`, `email` and `xml`, so a real first hook gains less.

## Mod path

The mod (`hooks/router.ts`) asks the runtime through the same launcher, one run per question, and waits for the answer:

| What | When the mod waits for it | Cold p50 | Cold p95 | Warm p50 | Warm p95 |
| :- | :- | -: | -: | -: | -: |
| `mod-start` | `session.start`, once per process | 183 | 197 | 54 | 64 |
| `mod-spawn` | `agent.spawn`, before each agent starts | 187 | 197 | 58 | 70 |
| `mod-usage` | `turn.complete`, after the turn, before the handler returns | 182 | 194 | 54 | 56 |

In process, loading the config, gathering the facts and running `router.decide` take 0.26 ms (p50) and 0.29 ms (p95). The mod's own handlers, timed in Node with the runtime's answer returned at once, take 0.001 to 0.004 ms (p50) and at most 0.005 ms (p95) for `agent.spawn`, `turn.start` and `turn.step`, so almost all of the time is starting Python. Not timed: before `mod-start` and `mod-spawn` the mod reads the five settings sources, waiting at most 2 seconds (`SETTINGS_MS` in `hooks/router.ts`), and its `tool.call` hook writes one empty mark file per `Agent` call. `turn.start` starts no Python.

## Context the plugin adds

Measured 2026-11-23 on Claude Code 2.1.330 with a headless Sonnet run that only says "ok" (`--setting-sources project`, a plugins root of its own): the first request's prompt (input plus cache read and write) was 19,286 tokens without the plugin, 20,013 with it before the latest cut, and 19,969 after. The plugin now adds 683 tokens, down from 728; an earlier cut, measured on Haiku, took it from 1,215 to 728. Each source was measured with that source removed (no `agents/` folder, no `skills/` folder, or no session-start hook).

| Source | When it's paid | Before | After |
| :- | :- | -: | -: |
| Session-start note (`hooks.session_start`) | At session start, resume, clear, compact and fork | 147 tokens | 128 |
| The seven role agents' names, descriptions and tools in the agent list | Once per session | 493 | 493 |
| The `route` skill's listing (the other four skills set `disable-model-invocation`, so theirs aren't in context, https://code.claude.com/docs/en/skills.md#control-who-invokes-a-skill) | Once per session | 84 | 61 |
| The note that the request names a model | Per prompt, only when the request names one | about 34 | about 34 |

The first three are measured, the last is rendered text at about 4 characters a token. On the fallback path each routed spawn also gets a one-line note.

A once-per-session source is cached on the first request and read from the cache on later ones, until it lapses or the conversation is compacted (https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md). On Sonnet 5.5 the 683 tokens cost about $0.0027 to write at the one-hour price (a Claude subscription's main conversation, within its plan) or $0.0017 at the five-minute price (an API key, usage credits or a cloud provider), then $0.00007 per later request (https://code.claude.com/docs/en/prompt-caching.md#which-ttl-each-request-gets, https://platform.claude.com/docs/en/about-claude/pricing.md). No subagent gets them: in kept benchmark transcripts the main loop's first request was 730 tokens bigger with the plugin and the subagents' first requests weren't. In the benchmark of commit `27b94e6` on a Sonnet session, the router lost by $0.009 to $0.012 a run in implementation, bugfix, cross-module and security review, and this text is about $0.003 of that. In the kept security-review pair, the router's subagent at `high` effort cost $0.0064 more than `defaults`' at the session's default.

## State locks a spawn takes

Every read-modify-write of a state file takes that file's lock ([State](ARCHITECTURE.md#state)), which is one more wait for agents started in one message and one more chance for a skipped write. `bench/overhead.py` counts them in the 1,000-call simulation (`locks` in `--json`), measured 2026-11-24 on the machine above:

| Path | Locks per spawn before | After | Files after |
| :- | -: | -: | :- |
| Mod (`mod-spawn`) | 3.0 | 2.0 | `ledger.jsonl`, `routes.json` |
| Fallback (`PreToolUse`) | 4.0 | 3.0 | the same, and `pending.json` for the rewrite waiting for its result |

Before is the code that also wrote each spawn's count and votes to `learned.json`, a state file that is gone. Now every spawn takes one lock per file it writes, which a test counts on a single spawn too (`SpawnPaths.test_a_spawn_takes_one_lock_per_state_file_it_writes`). Folding `pending.json` into the route would take the fallback to two, and waits until `locks.json` shows it matters.

## State size

1,000 routed calls were simulated with the real hook functions, 20 per session, even sessions on the fallback path and odd ones on the mod path. One call in four is a background agent finished by `SubagentStop`, every call adds a usage row, every fifth an edit and every fiftieth a model error.

| File | Bytes after 1,000 calls | Bounded | What bounds it |
| :- | -: | :- | :- |
| `ledger.jsonl` | 798,174 | yes | Halved when it passes 4 MiB (`store.LEDGER_MAX_BYTES`), so it reaches the cap after about 5,000 calls |
| `routes.json` | 200,006 | yes | Sessions written in the last day (`calls.KEPT_FOR`), at most the 200 most recently written (`store.KEEP_ENTRIES`), and 100 spawns per session (`store.KEPT_RECORDS`) |
| `done.json` | 114,486 | yes | Same as `routes.json` |
| `prompts.json` | 2,461 | yes | The 200 most recently written sessions |
| `edits.json` | 3,120 | yes | Same |
| `sessions.json` | 2,911 | yes | Same |
| `mod.json` | 1,738 | yes | Same |
| `launched.json` | 1,619 | yes | Same as `routes.json` |
| `pending.json` | 816 | yes | The 200 most recently written sessions, 100 calls each |
| `state.json` | 539 | yes | One health entry per model |

The "Bounded" column comes from a run of the script. The script lowers three caps (the ledger to 16 KiB, sessions to 5, calls per session to 10) and runs past them in two rounds, the second with twice the calls, once with one call per session (`max(40, calls // 5)` sessions, then twice as many: 200, then 400 at `--calls 1000`) and once with one long session (`max(50, calls // 5)` calls, then twice as many). A file is bounded when its second-round peak is at most 25% plus 256 bytes above its first. Removing each trim in turn, rerun 2026-11-24 with `--calls 1000`, proved the check: without the session cap all eight per-session files report unbounded, without the per-session call cap `routes.json` and `done.json` do, and without the ledger's halving `ledger.jsonl` does.
