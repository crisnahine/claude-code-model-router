# Security

Checked against the code on 2026-11-22 with Claude Code 2.1.330. Line numbers point at that code and drift as it changes.

## Reporting a problem

GitHub private vulnerability reporting is not turned on for this repository yet, so there is no private channel. Until there is, open an issue titled "Security contact" with no details in it, and the maintainer will reply with a private way to send them. Do not put a vulnerability, a repro or a secret in a public issue.

## What the plugin does

- It reads hook and mod event payloads, its own JSON state, the user's and the project's `model-router.json`, Claude Code settings files for `availableModels` and `deniedModels`, and the frontmatter of agent `.md` files (`agents.py:24-30`). It runs no code from the repository it works in.
- It writes only under its data directory: `CLAUDE_PLUGIN_DATA`, or `~/.claude/plugins/data/model-router-local` when that is unset (`store.py:23-27`). The one exception is the CI command `upstream-check`, which writes the file it is given (see Skills).
- On an agent call it sets `model` and `effort`. On a model request it sets `effort`. Both values come from the registry: a model name from `config/models.json` and an effort from the five levels in `config.py:16`.
- It never approves a tool call. It denies one thing, on the settings-hook path only: a call it already rewrote that came back without running, when the call's own model is above the user's cap. The denial names the model to use (`hooks.py:169-182`).

## Who can influence routing

| Source | What it can do | Limits on it |
| :- | :- | :- |
| The user's `~/.claude/model-router.json` and plugin options | Everything, including prices and the cap. It is trusted. | Nothing: it is the user's own file. |
| A repository's `.claude/model-router.json` | Add models, change levels and agent rules, lower `routing_mode`. | `_clean` keeps only well-formed `routing_mode`, `models`, `levels` and `agents` (`config.py:46-86`). Prices come from the user's file alone (`config.py:122-123`). The weakest `routing_mode` wins (`config.py:130-138`). A project model whose name sits inside or around a trusted model's name is dropped (`config.py:126-129`), and project models don't count under a cap (`config.py:103-108`). Open: it can raise levels and effort when no cap is set (r1-04, below). |
| Prompt and brief text | Raise or lower its own level by its wording. | Every target passes `registry.clamp` against the usable models, which apply the cap (`router.py:126`, `registry.py:186-212`). A model named in a prompt only adds a reminder for Claude (`hooks.py:125`). Without a cap, wording can cost money, but it can't get past the cap. |
| Other plugins | Their agents are routed like any other. Their hooks and mods run in their own order. | The router never changes which agent runs. Another plugin's deny or ask still applies. |
| Upstream feeds (changelog, docs index, model pages, Models API) | Fill the "what changed" report and model status. | Parsed as data and never run. An empty changelog or docs index counts as a failed fetch and leaves the last cache in place. |

## Network

Only `upstream.py` opens connections (`upstream.py:9`, `upstream.py:44-47`). It fetches five fixed HTTPS URLs (`upstream.py:17-21`):

- `code.claude.com`: the changelog RSS, `llms.txt` and `model-config.md`
- `platform.claude.com`: `model-deprecations.md`
- `api.anthropic.com`: `/v1/models`

It sends a fixed User-Agent and, to the Models API only, the API key. No prompt, brief, path, code or ledger content leaves the machine. TLS verification is urllib's default. Each request times out after 10 seconds (`upstream.py:23`).

Fetches run from the async `SessionStart` hook at most once a day, or when you run `/model-router:refresh`. Claude Code doesn't enforce `timeout` on an async hook (https://code.claude.com/docs/en/hooks.md#run-hooks-in-the-background), so the 10-second limit is what bounds it. Turning the `update_check` option off stops the daily fetch (`hooks.py:306-309`). `/model-router:refresh` still fetches when you run it.

The manifest lists the same three hosts under `network.hosts`. That field only declares them: it doesn't allow or block any host (https://code.claude.com/docs/en/plugins-reference.md).

## The API key

- Read only from `ANTHROPIC_API_KEY` (`settings.py:77`). It is kept out of the settings object's `repr` (`settings.py:32`) and is never written to state, the ledger or output.
- Sent only to `api.anthropic.com/v1/models`, and only when `ANTHROPIC_BASE_URL` is unset and no Bedrock, Vertex or Foundry provider is on (`upstream.py:95`). It goes in an `Authorization: Bearer` header, as https://platform.claude.com/docs/en/api/models/list.md shows.
- Redirects are refused whenever that header is set, so the key can't follow a 3xx to another host (`upstream.py:39-46`).

## State files

- Writes are atomic: a unique temp file, then a rename (`store.py:42-58`). Read-modify-write cycles take a lock file, wait up to 2 seconds, and take over a lock older than 10 seconds (`store.py:15-16`, `store.py:61-101`). Unreadable or malformed files read as empty (`store.py:30-35`).
- A hand-edited or corrupted state file can change routing decisions, health records and the spend report. It can't run code or get past the cap, because every target is clamped again on each call.
- The ledger keeps the first 120 characters of each agent call's description, plus levels, reasons and model names (`spawn.py:55`). Briefs and prompts themselves are not stored: a repeated call is recognized by a hash (`calls.py:24-28`). The data directory gets whatever permissions its parent has. If `CLAUDE_PLUGIN_DATA` points outside a private directory, other local users can read those descriptions.

## The mod

`hooks/router.ts` holds no routing logic. Each hook runs `sh <plugin root>/scripts/model-router.sh <command>` with an argument array, so no shell parses the input. The event goes in as JSON on stdin, with an 8-second timeout (`router.ts:26-29`). `ask()` never throws, and every hook has a `.catch` that passes the event on unchanged. Its in-memory maps keep at most 500 entries (`router.ts:8`).

## Skills

The `route`, `models`, `usage` and `refresh` skills grant `Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" *)` with `allowed-tools`. Claude can run what that grant covers without a prompt for the rest of the turn (https://code.claude.com/docs/en/skills.md#frontmatter-reference). The wildcard covers every launcher subcommand, including `upstream-check --baseline FILE --write` and `--json-out FILE`, which write to any path (`cli.py:99-111`). `route` can be started by Claude on its own. This is open item m1a-03, below.

`usage` passes `$ARGUMENTS` to its shell line unquoted (`skills/usage/SKILL.md:11`). That skill has `disable-model-invocation: true`, so only text the user typed reaches that line.

## GitHub workflows

- `ci.yml` has `contents: read` (`.github/workflows/ci.yml:8-9`).
- `upstream-watch.yml` has `contents: read` and `issues: write` (`.github/workflows/upstream-watch.yml:14-16`). It creates or edits one issue. Fetched text goes into the issue body through a file, never into a shell command.
- Neither workflow uses a secret other than `github.token`. Every action is pinned to a commit SHA.

## Open items

| Item | Status |
| :- | :- |
| m1a-03: the skill grants pre-approve every launcher subcommand, including the two that write files | In progress: each grant is being narrowed to the commands its skill runs |
| r1-04: whether a project's `.claude/model-router.json` may raise levels and effort, or only lower them | Waiting on the user. 0.1.0 allows raises, so an untrusted repository can raise cost when no cap is set |
| A private channel for reports | Waiting on the maintainer to turn on GitHub private vulnerability reporting |
