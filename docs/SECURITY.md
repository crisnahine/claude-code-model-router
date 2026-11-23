<!-- Page head, written by tools/docs_assets.py: edit its PAGES table, then run it. -->
<p align="center"><img src="assets/banner-security.svg" width="880" alt="Security: What reaches the network, and who can steer it?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="PERFORMANCE.md">Performance</a> &nbsp;&middot;&nbsp; Next: <a href="BENCHMARKS.md">Benchmarks</a></p>
<!-- End of page head. -->

# Security

Checked against the code on 2026-11-22 with Claude Code 2.1.330. Code is named by module and function, class or constant rather than line number, so an edit elsewhere in a file doesn't make a reference wrong. Python modules are under `plugins/model-router/scripts/modelrouter/` unless the path says otherwise. `SecurityClaims` in `tests/test_model_router.py` reruns this assessment's claims on every change: the repository-file limits, writes only under the data directory, no approvals, the network surface, the API key and its redirect guard, and the skill grants.

## Reporting a problem

GitHub private vulnerability reporting is not turned on for this repository yet, so there is no private channel. Until there is, open an issue titled "Security contact" with no details in it, and the maintainer will reply with a private way to send them. Do not put a vulnerability, a repro or a secret in a public issue.

## What the plugin does

- It reads hook and mod event payloads, its own JSON state, the user's and the project's `model-router.json`, the settings sources for `availableModels` and `deniedModels`, and the frontmatter of agent `.md` files (`agents.parse`). The mod reads each settings source and passes only the four allowlist keys to the runtime, never `env` or any other key (`modelSettings` in `hooks/router.ts`, `allowlist.remember`). A read that fails or takes more than two seconds is left out, and the runtime reads that source's file. The runtime cuts an allow list to 200 entries of 200 characters, and treats a policy deny list over that size as unreadable (no model open), never cutting it. It stores `modelOverrides` as a flag. The managed files are read on every call and bound the engine's answer, so another mod rewriting the `policy` read can't drop a managed deny; a managed file that has content but is unreadable or malformed leaves no model open (an empty one, or a folder among the drop-ins, is no policy) (`allowlist.from_layers`, `allowlist._policy`). A settings file must be a regular file under 1 MiB, so a symlink to a device can't hang a hook. The managed folder is fixed per OS and never comes from an environment variable, since a trusted repository's `env` block reaches hook processes (`allowlist.managed_root`). The local file is read at the repository root only when the root, its `.git` and its `.claude` belong to the current user, and for a worktree only through the layout git writes: a `.git` file naming `<main>/.git/worktrees/<name>` whose `commondir` leads back to `<main>/.git` (`allowlist._repository_root`). It runs no code from the repository it works in.
- It writes only under its data directory: `CLAUDE_PLUGIN_DATA`, or `~/.claude/plugins/data/model-router-local` when that is unset (`store.data_dir`). The one exception is the CI command `upstream-check`, which writes the file it is given (the `upstream-check` branch of `cli.main`) and which no skill pre-approves (see Skills).
- On an agent call it sets `model` and `effort`. On a model request it sets `effort`. The effort is one of the five levels in `config.EFFORTS`. The model is a name from the merged registry: `config/models.json` plus any models the user's or the repository's `model-router.json` adds (`config.base_config`, `config.load_config`). One exception is on the mod path only. When the `CLAUDE_CODE_USE_*` switches select a provider whose alias for the decided family runs an older model (`settings.resolve`, and `older_aliases` in `config/models.json`, from the alias table in https://code.claude.com/docs/en/model-config.md), `hooks.mod_spawn` sends that family's newest full Claude API ID from `newest`, such as `claude-sonnet-5-5`. It never sends an ID outside the decided family. It sends none when `ANTHROPIC_DEFAULT_<FAMILY>_MODEL`, `modelOverrides`, `availableModels` or `deniedModels` rules the version out, when the caller or the agent named an exact version, or while that ID is routed around after a model error (`hooks._newest`, `registry.newest_id`, `allowlist.Allowlist.permits_id`). Both keys come from the shipped file alone: `config._clean` keeps neither from a user's or a repository's `model-router.json`.
- It never approves a tool call. It denies one thing, on the settings-hook path only: a call it already rewrote that came back without running, when the call's own model is above the user's cap. The denial names the model to use (`hooks.pre_agent`, `hooks._refused_rewrite`).

## Who can influence routing

| Source | What it can do | Limits on it |
| :- | :- | :- |
| The user's `~/.claude/model-router.json` and plugin options | Everything, including prices and the cap. It is trusted. | Nothing: it is the user's own file. |
| A repository's `.claude/model-router.json` | Add models, change levels and agent rules, lower `routing_mode`. | `_clean` keeps only well-formed `routing_mode`, `models`, `levels` and `agents` (`config._clean`). Prices come from the user's file alone (`config.load_config`, with `config._prices`). The weakest `routing_mode` wins (`config.load_config`, by `config.MODES` order). A project model whose name sits inside or around a trusted model's name is dropped (`config.load_config`). Under a cap, project models don't count (`registry.available`, with `config.trusted_models`). By decision (2026-11-23), it may raise levels and effort. With no cap set, that and a model it adds with an unrelated name, which can be passed to the `Agent` tool as an agent's model (`registry.available`), can raise cost. `max_tier` caps the model of every pick, not the effort: `registry.available` drops models above the cap from the usable list that `ladder.pick` and `registry.select` choose from, and `registry.clamp` lowers a model named on the call, by a rule or by a pin. |
| Prompt and brief text | Raise or lower its own level by its wording. | Every target passes `registry.clamp` against the usable models, which apply the cap (`router.decide`, `registry.available`, `registry.clamp`). A model named in a prompt only adds a reminder for Claude (`hooks.named_model`, called and written as a note in `hooks.user_prompt`). A message that another Claude session or a teammate delivers isn't read as the user's request (`hooks.is_user_prompt`). Wording can't get past a cap. With no cap set, it can raise cost. |
| Other plugins | Their agents are routed like any other. Their hooks and mods run in their own order. | The router never changes which agent runs. Another plugin's deny or ask still applies. |
| Upstream feeds (changelog, docs index, model pages, Models API) | Fill the "what changed" report and model status. | Parsed as data and never run. An empty changelog or docs index counts as a failed fetch and leaves the last cache in place. |

## Network

Only `upstream.py` opens connections (`upstream.http_get`, the only place `urllib.request` is imported). It fetches five fixed HTTPS URLs (`upstream.CHANGELOG_RSS`, `DOCS_INDEX`, `MODEL_CONFIG_PAGE`, `DEPRECATIONS_PAGE`, `MODELS_API`):

- `code.claude.com`: the changelog RSS, `llms.txt` and `model-config.md`
- `platform.claude.com`: `model-deprecations.md`
- `api.anthropic.com`: `/v1/models`

It sends a fixed User-Agent and, to the Models API only, the API key. No prompt, brief, path, code or ledger content leaves the machine. TLS verification is urllib's default.

How long a fetch can take:

- Each connect and each socket read waits at most 10 seconds (`upstream.HTTP_TIMEOUT`, passed to the open in `upstream.http_get`). That limit is per operation, so on its own it doesn't bound a request whose server sends a byte at a time.
- A response body gets 10 seconds in all, checked between reads (the read loop in `upstream.http_get`). A body cut short is a failed request, not data.
- The whole fetch, up to five requests one after another, runs in a worker thread and is cut off at 25 seconds (`upstream.FETCH_BUDGET`, `upstream.fetch`). The hook process then exits and the last cache is kept. Tests: `Upstream.test_a_request_that_keeps_dripping_stops_at_its_deadline`, `Upstream.test_the_whole_fetch_stops_at_its_budget`, `Upstream.test_the_budget_covers_a_dripped_status_line_too`.

Fetches run from the async `SessionStart` hook once a day after a successful fetch (`upstream.refresh`, `upstream.CACHE_TTL_SECONDS`). After a failed fetch, session starts wait an hour before trying again, doubling with each failure in a row up to a day, and a success resets the wait (`upstream.FAILED_RETRY_SECONDS`, `upstream._retry_wait`, `upstream.refresh`); each try is capped at 25 seconds. `/model-router:refresh` fetches whenever you run it. Claude Code doesn't enforce `timeout` on an async hook (https://code.claude.com/docs/en/hooks.md#run-hooks-in-the-background), so the 25-second budget is what ends a stuck refresh. Turning the `update_check` option off stops the fetch at session start (`hooks.refresh_cache`).

The manifest lists the same three hosts under `network.hosts`. That field only declares them: it doesn't allow or block any host (https://code.claude.com/docs/en/plugins-reference.md).

## Credentials

- The API key is read only from `ANTHROPIC_API_KEY` (`settings.resolve`). It is kept out of the settings object's `repr` (the `api_key` field of `settings.Settings`) and is never written to state, the ledger or output.
- It is sent only to `api.anthropic.com/v1/models`, and only when `ANTHROPIC_BASE_URL` is unset and no Bedrock, Vertex or Foundry provider is on (`upstream.api_models`). It goes in an `Authorization: Bearer` header, as https://platform.claude.com/docs/en/api/models/list.md shows.
- Redirects are refused whenever that header is set, so the key can't follow a 3xx to another host (`RefuseRedirect` in `upstream.http_get`).
- The mod asks `$.session.authorize()` only whether the session holds a first-party credential, to decide whether a main-loop effort change keeps the cache. It checks whether the handle is null and never spends it (`hasFirstPartyCredential` in `hooks/router.ts`). Per the 2.1.330 mod API types, the secret never reaches the plugin; a handle can be spent only through `$.http.fetch`, which the mod doesn't call.

## State files

- Writes are atomic: a unique temp file, then a rename (`store.write_atomic`). A read-modify-write cycle takes a lock file and waits up to 2 seconds for it, taking over a lock older than 10 seconds. If it still can't get the lock, it goes ahead without it, so a hook never hangs a session (`store.locked`, `store.LOCK_WAIT_SECONDS`, `store.LOCK_STALE_SECONDS`). Two hooks writing one file at that moment can lose one update.
- Each state file carries a `schema` version (`store.SCHEMA`, `store.MIGRATIONS`). A file that fails to parse is moved aside as `<name>.corrupt-<UTC timestamp>` before it is replaced, never over an earlier copy. A file from a newer release, or one that can't be read, is never written (`store._read_state`, `store._set_aside`, `store.try_update`). `/model-router:models` lists both. Moved-aside copies stay in the data directory until someone deletes them.
- A hand-edited or corrupted state file can change routing decisions, health records and the spend report. It can't run code or get past the cap, because every target is clamped again on each call.
- The ledger keeps the first 120 characters of each agent call's description (`spawn.run`), plus levels, reasons, model names and token counts. Briefs and prompts themselves are not stored: a repeated call is recognized by a hash (`calls.identify`). The mod holds each turn's per-request usage in memory until the turn ends, token counts only (`turnRequests` in `hooks/router.ts`). The data directory gets whatever permissions its parent has. If `CLAUDE_PLUGIN_DATA` points outside a private directory, other local users can read those descriptions.
- `usage --json` and `models --json` print to stdout only: model names, statuses, counts, list-price dollars and state file paths, no brief text.

## The mod

`hooks/router.ts` holds no routing logic. Each hook runs `sh <plugin root>/scripts/model-router.sh <command>` with an argument array, so no shell parses the input. The event goes in as JSON on stdin, with an 8-second timeout (`ask` in `hooks/router.ts`). `ask()` never throws, and every hook has a `.catch` that passes the event on unchanged. Its in-memory maps keep at most 500 entries (`KEPT` in `hooks/router.ts`). It reads `prompt.submit`'s `origin.kind`, and for a typed `/skill` the command's name, arguments and the skill text `skill.prompt` renders, only to decide whether a turn's effort is raised. It never blocks or rewrites a prompt, and the ledger keeps only the command's name (the `prompt.submit` and `skill.prompt` hooks in `hooks/router.ts`, and `hooks.mod_turn`).

## Skills

Each skill's `allowed-tools` pre-approves only the launcher commands that skill runs, with the fixed `--data-dir "${CLAUDE_PLUGIN_DATA}"` prefix: `route` runs `policy` and `explain *`, `models` runs `models *`, `usage` runs `usage *`, and `refresh` runs `refresh --force` and `ack` (the `allowed-tools` frontmatter of `skills/route/SKILL.md`, `skills/models/SKILL.md`, `skills/usage/SKILL.md` and `skills/refresh/SKILL.md`). A grant applies for the turn that invokes the skill (https://code.claude.com/docs/en/skills.md#pre-approve-tools-for-a-skill). A compound command is split and each part checked, and an output redirect is checked against your file rules on its own (https://code.claude.com/docs/en/permissions.md), so no grant covers `upstream-check` or a write to another file. `SkillGrants.test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files` checks this.

`usage` passes `$ARGUMENTS` to its shell line unquoted (the `!` command line in `skills/usage/SKILL.md`). That skill has `disable-model-invocation: true`, so only text the user typed reaches that line.

## GitHub workflows

- `ci.yml` has `contents: read` (the top-level `permissions` of `.github/workflows/ci.yml`). Its `validate` job installs the latest `@anthropic-ai/claude-code` from npm, unpinned (the install step of its `validate` job). With read-only contents and no secrets, a bad release could fail CI but not change the repository.
- `upstream-watch.yml` has `contents: read` and `issues: write` (the top-level `permissions` of `.github/workflows/upstream-watch.yml`). It creates or edits one issue. Fetched text goes into the issue body through a file, never into a shell command.
- Neither workflow uses a secret other than `github.token` (the `GH_TOKEN` env of `.github/workflows/upstream-watch.yml`). Every action is pinned to a commit SHA.

## Open items

| Item | Status |
| :- | :- |
| A private channel for reports | Waiting on the maintainer to turn on GitHub private vulnerability reporting |
| The `validate` CI job installs an unpinned Claude Code | Open, low risk: it has read-only access and no secrets |
