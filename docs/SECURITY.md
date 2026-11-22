# Security

Checked against the code on 2026-11-22 with Claude Code 2.1.330. Line numbers point at that code and drift as it changes. Paths are under `plugins/model-router/scripts/modelrouter/` unless they say otherwise.

## Reporting a problem

GitHub private vulnerability reporting is not turned on for this repository yet, so there is no private channel. Until there is, open an issue titled "Security contact" with no details in it, and the maintainer will reply with a private way to send them. Do not put a vulnerability, a repro or a secret in a public issue.

## What the plugin does

- It reads hook and mod event payloads, its own JSON state, the user's and the project's `model-router.json`, Claude Code settings files for `availableModels` and `deniedModels`, and the frontmatter of agent `.md` files (`agents.py:24-27`). It runs no code from the repository it works in.
- It writes only under its data directory: `CLAUDE_PLUGIN_DATA`, or `~/.claude/plugins/data/model-router-local` when that is unset (`store.py:27-31`). The one exception is the CI command `upstream-check`, which writes the file it is given (`cli.py:130-141`) and which no skill pre-approves (see Skills).
- On an agent call it sets `model` and `effort`. On a model request it sets `effort`. The effort is one of the five levels in `config.py:19`. The model is a name from the merged registry: `config/models.json` plus any models the user's or the repository's `model-router.json` adds (`config.py:126-154`).
- It never approves a tool call. It denies one thing, on the settings-hook path only: a call it already rewrote that came back without running, when the call's own model is above the user's cap. The denial names the model to use (`hooks.py:209-210`, `hooks.py:214-228`).

## Who can influence routing

| Source | What it can do | Limits on it |
| :- | :- | :- |
| The user's `~/.claude/model-router.json` and plugin options | Everything, including prices and the cap. It is trusted. | Nothing: it is the user's own file. |
| A repository's `.claude/model-router.json` | Add models, change levels and agent rules, lower `routing_mode`. | `_clean` keeps only well-formed `routing_mode`, `models`, `levels` and `agents` (`config.py:49-89`). Prices come from the user's file alone (`config.py:138`). The weakest `routing_mode` wins (`config.py:145`, `config.py:152-153`). A project model whose name sits inside or around a trusted model's name is dropped (`config.py:142-144`). Under a cap, project models don't count (`registry.py:190-194`). Open: with no cap set, it can raise levels and effort, and a model it adds with an unrelated name can be passed to the `Agent` tool as an agent's model (`registry.py:186-199`), so it can raise cost (below). |
| Prompt and brief text | Raise or lower its own level by its wording. | Every target passes `registry.clamp` against the usable models, which apply the cap (`router.py:126`, `registry.py:186-212`). A model named in a prompt only adds a reminder for Claude (computed at `hooks.py:170`, written at `hooks.py:187-189`). A message that another Claude session or a teammate delivers isn't read as the user's request (`hooks.py:25-31`). Wording can't get past a cap. With no cap set, it can raise cost. |
| Other plugins | Their agents are routed like any other. Their hooks and mods run in their own order. | The router never changes which agent runs. Another plugin's deny or ask still applies. |
| Upstream feeds (changelog, docs index, model pages, Models API) | Fill the "what changed" report and model status. | Parsed as data and never run. An empty changelog or docs index counts as a failed fetch and leaves the last cache in place. |

## Network

Only `upstream.py` opens connections (`upstream.py:11`, `upstream.py:49-63`). It fetches five fixed HTTPS URLs (`upstream.py:19-23`):

- `code.claude.com`: the changelog RSS, `llms.txt` and `model-config.md`
- `platform.claude.com`: `model-deprecations.md`
- `api.anthropic.com`: `/v1/models`

It sends a fixed User-Agent and, to the Models API only, the API key. No prompt, brief, path, code or ledger content leaves the machine. TLS verification is urllib's default.

How long a fetch can take:

- Each connect and each socket read waits at most 10 seconds (`upstream.py:25`, `upstream.py:54`). That limit is per operation, so on its own it doesn't bound a request whose server sends a byte at a time.
- A response body gets 10 seconds in all, checked between reads (`upstream.py:52-63`). A body cut short is a failed request, not data.
- The whole fetch, up to five requests one after another, runs in a worker thread and is cut off at 25 seconds (`upstream.py:28`, `upstream.py:185-203`). The hook process then exits and the last cache is kept. Tests: `Upstream.test_a_request_that_keeps_dripping_stops_at_its_deadline`, `Upstream.test_the_whole_fetch_stops_at_its_budget`, `Upstream.test_the_budget_covers_a_dripped_status_line_too`.

Fetches run from the async `SessionStart` hook once a day after a successful fetch (`upstream.py:303-324`). After a failed fetch, session starts wait an hour before trying again, doubling with each failure in a row up to a day, and a success resets the wait (`upstream.py:286-324`); each try is capped at 25 seconds. `/model-router:refresh` fetches whenever you run it. Claude Code doesn't enforce `timeout` on an async hook (https://code.claude.com/docs/en/hooks.md#run-hooks-in-the-background), so the 25-second budget is what ends a stuck refresh. Turning the `update_check` option off stops the fetch at session start (`hooks.py:352-353`).

The manifest lists the same three hosts under `network.hosts`. That field only declares them: it doesn't allow or block any host (https://code.claude.com/docs/en/plugins-reference.md).

## Credentials

- The API key is read only from `ANTHROPIC_API_KEY` (`settings.py:77`). It is kept out of the settings object's `repr` (`settings.py:32`) and is never written to state, the ledger or output.
- It is sent only to `api.anthropic.com/v1/models`, and only when `ANTHROPIC_BASE_URL` is unset and no Bedrock, Vertex or Foundry provider is on (`upstream.py:107-112`). It goes in an `Authorization: Bearer` header, as https://platform.claude.com/docs/en/api/models/list.md shows.
- Redirects are refused whenever that header is set, so the key can't follow a 3xx to another host (`upstream.py:44-46`, `upstream.py:51`).
- The mod asks `$.session.authorize()` only whether the session holds a first-party credential, to decide whether a main-loop effort change keeps the cache. It checks whether the handle is null and never spends it (`hooks/router.ts:41-47`). Per the 2.1.330 mod API types, the secret never reaches the plugin; a handle can be spent only through `$.http.fetch`, which the mod doesn't call.

## State files

- Writes are atomic: a unique temp file, then a rename (`store.py:46-62`). A read-modify-write cycle takes a lock file and waits up to 2 seconds for it, taking over a lock older than 10 seconds. If it still can't get the lock, it goes ahead without it, so a hook never hangs a session (`store.py:65-107`). Two hooks writing one file at that moment can lose one update.
- Each state file carries a `schema` version (`store.py:19-20`). A file that fails to parse is moved aside as `<name>.corrupt-<UTC timestamp>` before it is replaced, never over an earlier copy. A file from a newer release, or one that can't be read, is never written (`store.py:109-172`). `/model-router:models` lists both. Moved-aside copies stay in the data directory until someone deletes them.
- A hand-edited or corrupted state file can change routing decisions, health records and the spend report. It can't run code or get past the cap, because every target is clamped again on each call.
- The ledger keeps the first 120 characters of each agent call's description (`spawn.py:55`), plus levels, reasons, model names and token counts. Briefs and prompts themselves are not stored: a repeated call is recognized by a hash (`calls.py:23-28`). The mod holds each turn's per-request usage in memory until the turn ends, token counts only (`hooks/router.ts:67`, `hooks/router.ts:171`). The data directory gets whatever permissions its parent has. If `CLAUDE_PLUGIN_DATA` points outside a private directory, other local users can read those descriptions.
- `usage --json` and `models --json` print to stdout only: model names, statuses, counts, list-price dollars and state file paths, no brief text.

## The mod

`hooks/router.ts` holds no routing logic. Each hook runs `sh <plugin root>/scripts/model-router.sh <command>` with an argument array, so no shell parses the input. The event goes in as JSON on stdin, with an 8-second timeout (`hooks/router.ts:26-29`). `ask()` never throws, and every hook has a `.catch` that passes the event on unchanged. Its in-memory maps keep at most 500 entries (`hooks/router.ts:8`). It reads `prompt.submit`'s `origin.kind`, and for a typed `/skill` the command's name, arguments and the skill text `skill.prompt` renders, only to decide whether a turn's effort is raised. It never blocks or rewrites a prompt, and the ledger keeps only the command's name (`hooks/router.ts:105-127`, `hooks.py:419-420`).

## Skills

Each skill's `allowed-tools` pre-approves only the launcher commands that skill runs, with the fixed `--data-dir "${CLAUDE_PLUGIN_DATA}"` prefix: `route` runs `policy` and `explain *`, `models` runs `models *`, `usage` runs `usage *`, and `refresh` runs `refresh --force` and `ack` (`skills/route/SKILL.md:6-8`, `skills/models/SKILL.md:6-7`, `skills/usage/SKILL.md:6-7`, `skills/refresh/SKILL.md:5-7`). A grant applies for the turn that invokes the skill (https://code.claude.com/docs/en/skills.md#pre-approve-tools-for-a-skill). A compound command is split and each part checked, and an output redirect is checked against your file rules on its own (https://code.claude.com/docs/en/permissions.md), so no grant covers `upstream-check` or a write to another file. `SkillGrants.test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files` checks this.

`usage` passes `$ARGUMENTS` to its shell line unquoted (`skills/usage/SKILL.md:12`). That skill has `disable-model-invocation: true`, so only text the user typed reaches that line.

## GitHub workflows

- `ci.yml` has `contents: read` (`.github/workflows/ci.yml:8-9`). Its `validate` job installs the latest `@anthropic-ai/claude-code` from npm, unpinned (`.github/workflows/ci.yml:57`). With read-only contents and no secrets, a bad release could fail CI but not change the repository.
- `upstream-watch.yml` has `contents: read` and `issues: write` (`.github/workflows/upstream-watch.yml:14-16`). It creates or edits one issue. Fetched text goes into the issue body through a file, never into a shell command.
- Neither workflow uses a secret other than `github.token` (`.github/workflows/upstream-watch.yml:33`). Every action is pinned to a commit SHA.

## Open items

| Item | Status |
| :- | :- |
| Whether a repository's `.claude/model-router.json` may raise levels and effort, and add models the router can pick, when no cap is set | Waiting on the user. Raises are allowed today, so an untrusted repository can raise cost when no cap is set. A cap (`max_tier`) bounds it: repository models don't count under a cap, and every pick stays at or below it |
| A private channel for reports | Waiting on the maintainer to turn on GitHub private vulnerability reporting |
| The `validate` CI job installs an unpinned Claude Code | Open, low risk: it has read-only access and no secrets |
