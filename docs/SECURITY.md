<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><img src="assets/banner-security.svg" width="880" alt="Security: What reaches the network, and who can steer it?"></p>

<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="PERFORMANCE.md">Performance</a> &nbsp;&middot;&nbsp; Next: <a href="CLAUDE_CODE_DOCS.md">Built on the docs</a></p>
<!-- End of page head. -->

# Security

Checked against the code on 2026-11-24, with Claude Code behavior as documented for 2.1.330. References use module, function, class and constant names, so an edit elsewhere in a file can't make one wrong. Python modules are in `plugins/model-router/scripts/modelrouter/`. `SecurityClaims` in `tests/test_model_router.py` reruns this page's main claims on every change.

## Reporting a problem

GitHub private vulnerability reporting is not turned on for this repository yet, so there is no private channel. Until there is, open an issue titled "Security contact" with no details in it, and the maintainer will reply with a private way to send them. Do not put a vulnerability, a repro or a secret in a public issue.

## What the plugin does

- It reads hook and mod event payloads, its own JSON state, the user's and the project's `model-router.json`, the settings sources for `availableModels` and `deniedModels`, and agent `.md` frontmatter (`agents.parse`). It runs no code from the repository it works in.
- From each settings source the mod passes only `availableModels`, `deniedModels`, `availableModelsMatch`, `modelOverrides`, `maxEffortLevel` and each `modelSettings` entry's `maxEffortLevel`, never `env` or any other key (`effortCaps` in `hooks/router.ts`, `allowlist.remember`). A read that fails or takes over two seconds is left out, and the runtime keeps what it last had for that source, else reads the file. A source that took over two seconds is read without waiting from then on, and its newest answer goes with each later command. An allow list is cut to 200 entries of 200 characters; a policy deny list over that size is unreadable and leaves no model open.
- The managed files are read on every call and bound the engine's answer, so another mod can't drop a managed deny. A managed file with content that is unreadable or malformed, or a policy list key that isn't a list, leaves no model open; an empty one, or a folder among the drop-ins, is no policy (`allowlist.from_layers`, `allowlist._policy`). A settings file must be a regular file of at most 1 MiB, so a symlink to a device can't hang a hook. The managed folder is fixed per OS and never comes from an environment variable, since a trusted repository's `env` block reaches hook processes (`allowlist.managed_root`).
- The local settings file is read at the repository root only when the root, its `.git` and its `.claude` belong to the current user, and never on Windows or for the home folder. A worktree's root comes only from the layout git writes (`allowlist._repository_root`).
- It writes only under its data directory: `CLAUDE_PLUGIN_DATA`, or `~/.claude/plugins/data/model-router-local` when that is unset (`store.data_dir`). The one exception is the CI command `upstream-check`, which overwrites its `--baseline` file under `--write` and writes the file `--json-out` names (`cli.main`) and which no skill pre-approves.
- On an agent call it sets `model` and `effort`. On a model request it sets `effort`. The effort is one of the five levels in `config.EFFORTS`. The model is a name from the merged registry: `config/models.json` plus models the user's or the repository's `model-router.json` adds (`config.load`).
- One exception, on the mod path only: when the `CLAUDE_CODE_USE_*` switches select a provider whose alias for the decided family runs an older model (`settings.resolve`, `older_aliases` in `config/models.json`), `mod.spawn` sends that family's newest full Claude API ID from `newest`, such as `claude-sonnet-5-5`. It never sends an ID outside the decided family, and none when `ANTHROPIC_DEFAULT_<FAMILY>_MODEL`, `modelOverrides`, `availableModels` or `deniedModels` rules the version out, when the caller or agent named an exact version, or while that ID is routed around after a model error (`mod._newest`, `Registry.newest_id`, `allowlist.Allowlist.permits_id`). `newest` and `older_aliases` come from the shipped file alone: `config._clean` keeps neither from a user's or a repository's file.
- It never approves a tool call. It denies one thing, on the settings-hook path only: a call it already rewrote that came back without running, when the call's own model is above the user's cap. The denial names the model to use (`fallback.route`, `fallback._guard`).

## Who can influence routing

| Source | What it can do | Limits on it |
| :- | :- | :- |
| The user's `~/.claude/model-router.json` and plugin options | Everything, including prices and the cap. | None: it is the user's own file. |
| A repository's `.claude/model-router.json` | Add models, raise levels, raise agents with rules, lower `routing_mode`. | `_clean` keeps only well-formed `routing_mode`, `models`, `levels` and `agents`, and `config.build` lists what it dropped (`Registry.problems`). Prices come from the user's file alone (`config._prices`). The weakest `routing_mode` wins. A model whose name sits inside or around a trusted model's name is dropped. The router never picks a project model, whatever it claims, so one runs only when a call or the user's own rule names it (`Model.project`, `Registry.select`, `router.decide`), and it never stands in for a model that can't run (`Registry.clamp`), or counts at all under a cap (`Registry.available`). A level's `needs` and `effort` can be raised, never lowered: a lower value is dropped without an error (`config._raises_only`), and a `needs` that isn't a finite number is dropped from every file (`config._number`). An agent rule only raises: `level` is a floor, `model` applies only when it isn't weaker than what the call would run on without it, and `route: false` is ignored (`config._rules`). The ledger's `held` field and `/model-router:models` list what was held (`Registry.held`). With no cap set, a raised level or a model a call passes to the `Agent` tool can raise cost. `max_tier` caps the model of every pick, not the effort: `Registry.available` drops models above the cap from what `ladder.pick` and `Registry.select` choose from, and `Registry.clamp` lowers a model named on the call, by a rule or by a pin. More in [Architecture, Trust](ARCHITECTURE.md#trust). |
| Prompt and brief text | Nothing directly: the router never reads a brief. A model the caller names passes the same cap. | Every target passes `Registry.clamp` against the usable models, which apply the cap (`router.decide`, `Registry.available`). A model named in a prompt only adds a reminder for Claude (`request.named_model`, written as a note in `hooks.user_prompt`). A message another Claude session or a teammate delivers isn't read as the user's request (`intake.is_user_prompt`). A named model can't get past a cap, and with none set it can raise cost. |
| Other plugins | Their agents are routed like any other. Their hooks and mods run in their own order. | The router never changes which agent runs. Another plugin's deny or ask still applies. |
| Upstream feeds (changelog, docs index, model pages, Models API) | Fill the "what changed" report and model status. | Parsed as data and never run. An empty changelog or docs index counts as a failed fetch and leaves the last cache in place. |

## Network

Only `upstream.py` opens connections (`upstream.http_get`, the only place `urllib.request` is imported). It fetches five fixed HTTPS URLs (`upstream.CHANGELOG_RSS`, `DOCS_INDEX`, `MODEL_CONFIG_PAGE`, `DEPRECATIONS_PAGE`, `MODELS_API`) on three hosts, which the manifest lists under `network.hosts`:

- `code.claude.com`: the changelog RSS, `llms.txt` and `model-config.md`
- `platform.claude.com`: `model-deprecations.md`
- `api.anthropic.com`: `/v1/models`

The manifest field only declares the hosts, it doesn't allow or block any (https://code.claude.com/docs/en/plugins/manifest-reference.md#fields). It sends a fixed User-Agent and, to the Models API only, the API key. No prompt, brief, path, code or ledger content leaves the machine. TLS verification is urllib's default. Two of the plugin's agents can also reach the web through Claude Code's own tools, under your permission rules: `architect` has `WebFetch`, and `docs-researcher` has `WebFetch` and `WebSearch`.

Time limits, each tested in `Upstream`: a connect or socket read waits at most 10 seconds (`upstream.HTTP_TIMEOUT`), a response body gets 10 seconds in all (`upstream.http_get`), and the whole fetch, up to five requests in turn, runs in a worker thread cut off at 25 seconds (`upstream.FETCH_BUDGET`, `upstream.fetch`), which exits the hook process and keeps the last cache. Claude Code doesn't enforce `timeout` on an async hook (https://code.claude.com/docs/en/hooks.md#run-hooks-in-the-background), so that budget is what ends a stuck refresh.

The async `SessionStart` hook fetches once a day after a success (`upstream.refresh`). After a failure, session starts wait an hour, doubling with each failure in a row up to a day, and a success resets the wait (`upstream._retry_wait`). `/model-router:refresh` fetches whenever you run it. The `update_check` option off stops the session-start fetch (`hooks.refresh_cache`).

## Credentials

- The API key is read only from `ANTHROPIC_API_KEY` (`settings.resolve`). It is kept out of the settings object's `repr` (the `api_key` field of `settings.Settings`) and is never written to state, the ledger or output.
- It is sent only to `api.anthropic.com/v1/models`, and only when `ANTHROPIC_BASE_URL` is unset and none of `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or `CLAUDE_CODE_USE_FOUNDRY` is on (`upstream.api_models`). It goes in an `Authorization: Bearer` header, as https://platform.claude.com/docs/en/api/models/list.md shows.
- Redirects are refused whenever that header is set, so the key can't follow a 3xx to another host (`RefuseRedirect` in `upstream.http_get`).

## State files

- Writes are atomic: a unique temp file, then a rename (`store.write_atomic`). A read-modify-write takes a lock file and waits up to 1 second for it, with backoff, taking over a lock older than 10 seconds. If it still can't get the lock, it skips that write and counts it: a hook never hangs a session or writes without the lock, and the skip loses that one update (`store.locked`, `store.LOCK_WAIT_SECONDS`, `store.LOCK_STALE_SECONDS`).
- Each state file carries a `schema` version (`store.SCHEMA`). A file that fails to parse is moved aside as `<name>.corrupt-<UTC timestamp>` before it is replaced, and the three newest copies of each file are kept (`store._set_aside`, `store.KEEP_SET_ASIDE`). A file from a newer release, or one that can't be read, is never written (`store.try_update`). `/model-router:models` lists both.
- A hand-edited or corrupted state file can change routing decisions, health records and the spend report. It can't run code or get past the cap, since every target is clamped again on each call.
- The ledger keeps session ids, levels, reasons, model names, token counts and list-price costs (`ledger.spawn`). Briefs, prompts and descriptions are not stored: a call is recognized by a hash of its agent and prompt (`calls.identify`), and its task by a hash of its agent and description (`calls.task`). Records an older release wrote with the description in `done.json` and `launched.json` are never read, and go with their session a day after its last write (`calls.KEPT_FOR`). The mod holds each turn's per-request token counts in memory until the turn ends (`turnRequests` in `hooks/router.ts`). The data directory gets whatever permissions its parent has, so if `CLAUDE_PLUGIN_DATA` points outside a private directory, other local users can read the ledger and state.
- `usage --json` and `models --json` print model names, statuses, counts, list-price dollars and state file paths, never brief text.

## The mod

`hooks/router.ts` holds no routing logic. It runs `sh <plugin root>/scripts/model-router.sh <command>` with an argument array, so no shell parses the input, and sends the event as JSON on stdin with an 8-second timeout (`ask`). `ask()` never throws, and every hook has a `.catch` that passes the event on unchanged. Its in-memory maps keep at most 500 entries (`KEPT`). It never blocks or rewrites a prompt. Its one file write is an empty mark under `mod-calls/` in `CLAUDE_PLUGIN_DATA` for each `Agent` call while its runtime answers, named by the call's `tool_use_id`, and only when that id is 1 to 128 letters, digits, `_` or `-` (`PLAIN_ID`), so it can't name a path outside that folder. The fallback checks the same pattern before it removes a mark (`mod.routes`).

## Skills

Each skill's `allowed-tools` pre-approves only the launcher commands that skill runs, with the fixed `--data-dir "${CLAUDE_PLUGIN_DATA}"` prefix (the frontmatter of `skills/*/SKILL.md`): `route` runs `policy` and `explain *`, `models` runs `models *`, `usage` runs `usage *`, and `refresh` runs `refresh --force`, `ack` and `date +%F`. `ship` pre-approves nothing. A grant applies for the turn that invokes the skill (https://code.claude.com/docs/en/skills.md#pre-approve-tools-for-a-skill). Claude Code checks each part of a compound command and an output redirect against your file rules on its own (https://code.claude.com/docs/en/permissions.md), so no grant covers `upstream-check` or a write to another file. `SkillGrants.test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files` checks this.

`usage` passes `$ARGUMENTS` to its `!` shell line unquoted (`skills/usage/SKILL.md`). It has `disable-model-invocation: true`, so only text the user typed reaches that line.

## GitHub workflows

Every action in all three workflows is pinned to a commit SHA.

- `ci.yml` has `contents: read` (its top-level `permissions`). Its `validate` job installs `@anthropic-ai/claude-code` from npm at pinned versions, 2.1.329 and 2.1.330 (the job's `matrix`), and its `eval` job installs 2.1.330. The `typecheck` job downloads Claude Code's published mod types at a pinned commit and installs `typescript` and `@types/node` at pinned versions (`tests/mod/typecheck.sh`). With read-only contents, a bad release could fail CI and can't change the repository.
- `ci.yml`'s `eval` job runs only when started by hand (`workflow_dispatch`), and it is the only job given a secret: `ANTHROPIC_API_KEY`, for the live model calls of `claude plugin eval`, with `--max-cost-usd 2` as a ceiling on the estimated list-price cost (https://code.claude.com/docs/en/plugin-evals.md#command-options). It runs the checked-out branch's plugin code with `--trust-plugin`, so start it only on a branch you trust.
- `upstream-watch.yml` runs daily and by hand with `contents: read` and `issues: write`. It creates or edits one issue, and fetched text goes into the issue body through a file, never into a shell command. Its only secret is `github.token` (the `GH_TOKEN` env).
- `pages.yml` runs on pushes to `main` that touch `site/**` or `pages.yml`, and by hand. It has `contents: read`, and only its `deploy` job adds `pages: write` and `id-token: write`. It uses no secret.

## Open items

| Item | Status |
| :- | :- |
| A private channel for reports | Waiting on the maintainer to turn on GitHub private vulnerability reporting |
