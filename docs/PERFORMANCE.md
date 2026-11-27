<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="SECURITY.md">Security</a> &nbsp;&middot;&nbsp; Next: <a href="CLAUDE_CODE_DOCS.md">Built on the docs</a></p>
<!-- End of page head. -->

# Performance

Times and sizes come from `python3 bench/overhead.py` (`--json` for JSON), which makes no paid call and works in throwaway folders.

Measured on an Apple M3 Pro (11 cores), macOS 27.2.1, Node 22.22.3 and Claude Code 2.1.332, with other work running (load average about 5), once with Apple's Python 3.9.6 as `python3` and once with Homebrew's Python 3.13.14.

## Hook time

Warm p50 milliseconds per call over 30 runs, each through `sh scripts/model-router.sh`:

| Command | 3.9.6 | 3.13 |
| :- | -: | -: |
| Each of the nine settings hooks in `hooks.json` | 80 to 93 | 60 to 64 |
| `mod-start`, at the mod's `session.start` and the first main-loop turn after `/clear`, `/resume` or `/branch` | 84 | 61 |
| `mod-spawn`, at `agent.spawn` | 86 | 63 |
| `mod-usage`, at each subagent turn's `turn.complete` | 83 | 63 |

p95 ran 84 to 221 ms on 3.9.6 and 62 to 93 ms on 3.13. The launcher's loop with no router code after it (`sh` and one `python3 -c pass`) takes 40 and 48 ms. Apple's 3.9.6 writes no bytecode under the launcher's `-I` and ships none for its standard library, so the launcher gives it a private cache in `~/.cache/model-router/pycache`. Without the cache, a hook took about twice as long in a side-by-side run (189 to 222 ms against 97 to 100 ms).

The routing decision itself (`config.load`, `spawn.facts`, `router.decide`) takes about 1.1 ms in process, and the mod's `agent.spawn` handler 0.05 to 0.07 ms in Node. So nearly all of each figure is starting Python and loading the plugin. Not timed: the mod's settings reads, which wait at most 2 s (`SETTINGS_MS` in `hooks/router.ts`).

No hook runs async, and the mod waits for each answer it routes by. In a session with a person at the prompt, the mod also runs `mod-status` (the status line) after each spawn and each main-loop turn, `ps` and `mod-check` once at the start, and `mod-end` at the end, and `/model-router` waits on one `mod-status`. Each starts a process like the commands above, and they weren't timed. The status refresh and the session check run after the hook that asks for them has returned, so no spawn or turn waits on them, and a spawn during a refresh adds one more refresh, not one each. Summed at p50:

| What | Commands | 3.9.6 | 3.13 |
| :- | :- | -: | -: |
| A prompt | `user-prompt`, `stop` | 164 | 123 |
| An `Agent` call through the mod, one subagent turn | `pre-agent`, `mod-spawn`, `post-agent`, `agent-stopped`, `mod-usage` | 418 | 316 |
| An `Agent` call with no mod | `pre-agent`, `post-agent`, `agent-stopped` | 250 | 189 |
| An edit | `post-edit` | 82 | 60 |

## State on disk

After 1,000 routed calls through the real hook functions, half the sessions on each path (the 3.9.6 run; the 3.13 run is within 10 bytes per file):

| File | Bytes | Bounded by |
| :- | -: | :- |
| `ledger.jsonl` | 799,299 | 30 days, trimmed once a day; past 4 MiB, replaced usage rows go, then the older half (about 5,200 calls) |
| `routes.json`, `done.json`, `launched.json` | 184,869, 96,289, 1,597 | 200 sessions, a day idle, 100 records each |
| `allowlist.json`, `edits.json`, `sessions.json` | 5,510, 3,119, 2,901 | 200 sessions |
| `pending.json` | 801 | 200 sessions, 100 calls each |
| `state.json` | 383 | one entry per model or failed full ID |

The script also lowers the caps and runs past them twice. Every file stayed bounded, and `OverheadBench.test_the_overhead_bench_reports_every_hook_and_state_file` holds `ledger.jsonl` and `state.json` to it. A spawn takes one lock per file it writes: 2 through the mod, 3 without it (`SpawnPaths.test_a_spawn_takes_one_lock_per_state_file_it_writes`).

## Context

Characters of plugin text Claude gets. Tokens weren't measured.

| Text | When | Characters | Test budget |
| :- | :- | -: | -: |
| Session-start note (`hooks._rules`) | each start, resume, clear, compact and fork | 634 with no option set, 206 to 653 over 32 option mixes | 720 |
| The same note, after two newlines, in the Agent tool's description (mod only) | all session | 636 with no option set, at most 655 | 720, the note's |
| The table for a script's stages (`hooks._workflow_rules`), after two newlines, in the Workflow tool's description (mod only) | all session | 472 with no option set | 540 |
| The fallback's note on a call it rewrites (`fallback._note`) | with that call's result, no mod | 84 to 181 | none |
| A redo with no step left (`spawn.redo_note`) | after that call's result | 106 to 118 | none |
| The five agents' descriptions | all session | 765 | 930 |
| The `route` skill's description | all session | 199 | 200 |
| A note that the prompt names a model (`hooks.user_prompt`) | on such a prompt | 135 to 139 | none |
| The Stop ask (`gates.unverified_edit`) | once per batch of unchecked edits | 516 | none |
| The `route` skill's text and its policy output | when it runs | about 4,940 and 1,483 | none |

With the mod, the model table appears three times: in the session context, in the Agent tool's description and in the Workflow tool's, so a session with no option set carries 1,742 characters of it. `Notes.test_every_note_stays_within_budget` walks the mixes of `max_tier`, `CLAUDE_CODE_SUBAGENT_MODEL`, its FORCE switch and the two effort variables. `Hooks.test_the_text_in_context_every_session_stays_within_budget` holds the descriptions. `models` and `usage` set `disable-model-invocation`, so their descriptions stay out ([skills](https://code.claude.com/docs/en/skills.md#control-who-invokes-a-skill)). With `routing_mode` off there is no note and no Stop ask.
