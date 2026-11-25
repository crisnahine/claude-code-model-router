# Plugin packaging and versioning

2026-11-25: this note checks Model Router as it stood on 2026-11-24, before the 0.2.0 redesign. "What we built" describes that code, and some of what it names is gone or works differently now. What the code does now is in the [architecture review](../architecture-review.md).

Researched 2026-11-24 on Claude Code 2.1.331 (`claude --version`). Every page was fetched that day as `.md`. `claude plugin validate` was not run for this page; CI runs it.

Short names used below:

- `[manifest]` https://code.claude.com/docs/en/plugins/manifest-reference.md
- `[marketplace]` https://code.claude.com/docs/en/plugins/marketplace-reference.md
- `[cli]` https://code.claude.com/docs/en/plugins/cli-reference.md
- `[loading]` https://code.claude.com/docs/en/plugins/loading.md
- `[publish]` https://code.claude.com/docs/en/plugins/publish.md
- `[host]` https://code.claude.com/docs/en/plugins/host-marketplace.md
- `[components]` https://code.claude.com/docs/en/plugins/components.md
- `[hooks]` https://code.claude.com/docs/en/hooks.md
- `[mods-ref]` https://code.claude.com/docs/en/plugins/mods/reference.md
- `[mods-create]` https://code.claude.com/docs/en/plugins/mods/create.md
- `[mods-overview]` https://code.claude.com/docs/en/plugins/mods/overview.md
- `[changelog]` https://code.claude.com/docs/en/changelog.md
- `[mod-types]` the generated type file `plugins/model-router/.claude-plugin/types/claude-code/index.d.ts` in the main checkout, headed "Written by Claude Code 2.1.330". The worktree has no copy, because the folder's own `.gitignore` ignores everything in it.

## What we built

- `plugins/model-router/.claude-plugin/plugin.json` sets `name`, `displayName`, `version` (`0.1.2`), `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `engines.claude-code` (`>=2.1.329`), `network.hosts` (`code.claude.com`, `platform.claude.com`, `api.anthropic.com`) and eight `userConfig` options: four string pickers, each with `options` and a `default` from its list, and four booleans with a `default`.
- `.claude-plugin/marketplace.json` sets `name` (`claude-code-model-router`), `description`, `owner`, and one entry with `name`, a relative `source` (`./plugins/model-router`), its own `description`, `category` and `tags`. It sets no `version` and no `engines`.
- `hooks/hooks.json` lists `"modules": ["./router.ts"]` and settings hooks on eight events, all in exec form (`"command": "sh"` plus `args`), one of them `async`.
- `hooks/router.ts` `register(on, options)` turns `options` into `CLAUDE_PLUGIN_OPTION_<KEY>` variables (`optionEnv`) and adds them to `$.process.hookEnv()` for each `$.process.run`. Every hook has a `.catch` that calls `next(e)`.
- `scripts/modelrouter/settings.py` `resolve` reads `CLAUDE_PLUGIN_OPTION_<KEY>`. `record`, called from `hooks.py` at SessionStart, copies the options into `state.json` for skill commands, which run through the Bash tool.
- `scripts/modelrouter/store.py` `data_dir` uses `CLAUDE_PLUGIN_DATA`, else `~/.claude/plugins/data/model-router-local`. Each state file carries `schema` (`SCHEMA = 1`). `_read_state` runs `MIGRATIONS` up to it, and a file from a newer release reads as `newer` and is never written (`try_update`, `remove_state_file`).
- `.github/workflows/ci.yml` runs `claude plugin validate --strict` on the marketplace root and on the plugin folder, on 2.1.329 and 2.1.330. `RELEASING.md` keeps the version in `plugin.json` only and tags `v<version>`. `tests/test_model_router.py` `test_the_claude_code_floor_is_the_version_the_manifest_and_teammate_effort_need` pins the floor.
- On `main` (first-parent history of `plugins/model-router/`) the version went `0.1.0` (8ec8eec), `0.1.1` (27b94e6), `0.1.2` (3f036a1), back to `0.1.0` (da1e68c), then `0.1.2` again (9e247d3). Four commits changed plugin files with no version change: b836649 (`store.py`), e2ab55f (`plugin.json` options), c694a43 (three `SKILL.md` files and the plugin README) and 29672df (plugin README).

## What the sources say

### Manifest fields we set

| Field | What the docs say | Source | Our use |
| :- | :- | :- | :- |
| `name` | "Plugin identifier, required. Use kebab-case." A name that starts with `claude-` is an error | `[manifest]` Fields, `name` | Matches |
| `displayName` | "Name shown in UI in place of `name`" | `[manifest]` Fields | Matches |
| `version` | "Version string. Setting it keeps users on that version until you change it"; "A version string, not checked against semver." | `[manifest]` Fields, `version` | Field matches; see Versions and updates |
| `description` | "Short explanation of what the plugin provides" | `[manifest]` Fields | About 600 characters, and the marketplace entry's own `description` replaces it in listings (below) |
| `author` | "`name`, which is required, plus optional `email` and `url`" | `[manifest]` Fields | Matches |
| `homepage` | "Documentation URL. Must parse as a URL" | `[manifest]` Fields | Matches |
| `repository` | "Source repository URL. Not validated" | `[manifest]` Fields | Matches |
| `license` | "SPDX identifier such as `MIT` or `Apache-2.0`" | `[manifest]` Fields | Matches |
| `keywords` | "Discovery tags" | `[manifest]` Fields | Matches |
| `engines` | "The Claude Code versions that the plugin works on, as a semver range under the `claude-code` key ... Requires Claude Code v2.1.323 or later. To keep the plugin working on v2.0.15 through v2.1.75, omit `engines`. Those versions reject the manifest with an `Unrecognized key` error when it has the field" | `[manifest]` Fields | Matches |
| `network` | "The hosts your plugin reaches ... `claude plugin validate` lists them. The field doesn't allow or block any host. Requires Claude Code v2.1.328 or later" | `[manifest]` Fields | Matches. The three hosts are the ones `upstream.py` fetches (`CHANGELOG_RSS`, `DOCS_INDEX`, `MODEL_CONFIG_PAGE`, `DEPRECATIONS_PAGE`, `MODELS_API`) |
| `userConfig` keys | "Keys are identifiers made of letters, digits, and underscores, and can't start with a digit." | `[manifest]` User configuration | Matches |
| Option fields | `type`, `title` and `description` are required; `default` is "a string, number, boolean, or array of strings"; `options` is "For `string`, the values the field accepts, shown as a picker in `/config` ... Requires Claude Code v2.1.271 or later"; "Set `default` to one of the listed values"; "Each option is a plain label of 1 to 64 characters" | `[manifest]` User configuration, Limit a field to fixed options | Matches. Each `default` is in its list (`test_each_choice_option_is_a_picker_the_runtime_accepts`) |

- Unknown keys: "An unrecognized top-level key is stripped, and the plugin loads." `userConfig` options "are strict. An unknown key inside one is an error, and the plugin doesn't load" `[manifest]` Unrecognized fields.
- Deprecated: the changelog deprecates "a plugin's `agent` setting" (2.1.323), and a top-level `themes` or `monitors` key "still loads, with a `claude plugin validate` warning" `[changelog]`, `[manifest]` Fields. We use none of them. Nothing we set is deprecated.
- The manifest's `types` field is for "the `$.state` values and `$` nouns of a mod" `[manifest]` Fields. The mod uses neither, so it needs no `types`.

### Marketplace fields we set

| Field | What the docs say | Source | Our use |
| :- | :- | :- | :- |
| `name` | "letters, digits, `.`, `_`, and `-`, starting with a letter or digit", plus a reserved list and "Names that impersonate an official marketplace: names such as `official-claude-plugins` or `claude-plugins-v2`" | `[marketplace]` Top-level fields, Reserved names | Not on the reserved list |
| `description` | "Marketplace description shown to users. `claude plugin validate` warns when it's missing" | `[marketplace]` Top-level fields | Matches |
| `owner` | "Maintainer information. `name` is required; `email` and `url` are optional" | `[marketplace]` Top-level fields | Matches |
| Entry `name` | "Give the entry the same `name` as `plugin.json`" | `[publish]` Add the marketplace file | Matches |
| Entry `source` | A relative path "Must start with `./`" | `[marketplace]` Plugin sources | Matches |
| Entry `description` | "Shown in `/plugin` listings and details". "For a field you set on the entry, users see the entry's value, even when `plugin.json` sets a different one." "Before install, Claude Code can read `plugin.json` only for entries with a relative-path source" | `[marketplace]` Plugin entries, Display fields | Documented, but it hides the `plugin.json` text, and the two already say different things |
| Entry `category`, `tags` | "Free-form category for organizing the catalog"; "Free-form tags for search" | `[marketplace]` Plugin entries | Matches |
| No entry `version` | "Don't set `version` in both `plugin.json` and the marketplace entry." | `[host]` Release a new version | Matches |
| No entry `engines` | "To keep the marketplace working on v2.0.15 through v2.1.75, omit `engines` from every entry. Those versions reject the whole `marketplace.json` when any entry has the field" | `[marketplace]` Plugin entries | Fine: `plugin.json` carries the floor |

- "Claude Code ignores an unknown top-level key or plugin-entry key rather than rejecting it, so a typo loads silently. `claude plugin validate` reports each unknown key as a warning." `[marketplace]` Marketplace file. CI's `--strict` turns such a typo into a failure.

### hooks.json and the hooks module

- "`modules`: an array with one path, relative to this file, to the hooks module ... Can also hold settings hooks under `hooks`." The module "Exports `register(on, options)`. Named `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.mts`, `.cts`, or `.tsx`." `[mods-ref]` Files. `[components]` says the same: "list a module file under a `modules` key in the same `hooks/hooks.json`. A plugin with one is a mod."
- "`options` holds the values of the `userConfig` fields the manifest declares, with defaults filled in." `[mods-ref]` Files. `$.process.hookEnv()` "Returns the variables that name the session and this plugin, as its command hook gets them" and lists `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA` and others, with no `CLAUDE_PLUGIN_OPTION_*` `[mod-types]`. So `optionEnv` is needed. "`hookEnv` requires Claude Code v2.1.326 or later." `[mods-ref]` mods API methods.
- "The options are fixed for this activation: a change to them reloads the plugin and `register` runs again with the new object." `[mod-types]` (`Register`).
- Settings hooks: "every hook process receives `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` in its environment, plus `CLAUDE_PLUGIN_OPTION_<KEY>` for each user configuration value" `[components]` Hooks. Options are "exported to hook processes"; `${user_config.KEY}` is "substituted in ... skill and agent content", where "only non-sensitive values are substituted" `[manifest]` Reference a saved value. "The variables aren't present in the environment of commands Claude runs through the Bash tool" `[manifest]` Where each variable resolves. So skills can't read `CLAUDE_PLUGIN_OPTION_*`, which is why `settings.record` exists. `${user_config.KEY}` in the skill body is the documented other route.
- Exec form: "A command hook runs as exec form when `args` is set" and "On Windows, exec form requires `command` to resolve to a real executable such as a `.exe`." `[hooks]` Exec form and shell form.
- "PostModelSwitch requires Claude Code v2.1.251 or later." SessionStart matcher values are "`startup`, `resume`, `clear`, `compact`, `fork`" `[hooks]`. Both fit the floor and our matcher.

### The floor on older builds

- 2.1.323: "Added `engines["claude-code"]` to `plugin.json`: plugins outside their stated Claude Code version range are not installed, updated or loaded, and install and update pick the newest version that fits" `[changelog]`.
- `engines` "Requires Claude Code v2.1.323 or later" `[manifest]` Fields. An unknown top-level key "is stripped, and the plugin loads" `[manifest]` Unrecognized fields. "If you declare `options` on any field, users on Claude Code versions before v2.1.271 can't load the plugin." `[manifest]` Limit a field to fixed options.
- Read together: below 2.1.271 the plugin doesn't load, 2.1.323 and later hold the floor, and 2.1.271 to 2.1.322 strip `engines` and load the plugin. That middle range is my reading of the quotes above; no page states it. On 2.1.287 to 2.1.325 the mod's `ask` throws on the missing `hookEnv` inside its `try`, returns no pick, and the mod changes nothing (from reading `router.ts`, not run).
- Why the floor is 2.1.329: `network` needs 2.1.328 `[manifest]`; `hookEnv` needs 2.1.326 `[mods-ref]`; "Mods are on by default. In the terminal, use Claude Code v2.1.287 or later." `[mods-overview]`; and 2.1.329 "Fixed the Agent tool's `effort` parameter being ignored for named teammates in agent teams" `[changelog]`. So `>=2.1.329` is the lowest build that has every field and call the plugin uses.

### Newer builds

- The type file opens with "EARLY ACCESS: this surface may change between releases without notice." `[mod-types]`
- "The events and methods can change between releases, so trust these files over any page" and "your README is the place to say which Claude Code version you tested with." `[mods-create]` Get the types for your build, Share your mod. The README says "Tested live on 2.1.330 and 2.1.331".
- 2.1.323: "Added type checking of a plugin's TypeScript hooks module to `claude plugin validate`; each finding is a warning, and `--strict` fails on it" `[changelog]`. Validate "runs the same static analysis on the hooks module's source that Claude Code runs when it loads a mod" `[mods-create]` Check what Claude Code reads from your mod.
- `claude plugin validate --json` lists `gatingHooks`: "whether each mod hook that can refuse an action, such as a `tool.call` hook, has a `.catch` handler" `[cli]` plugin validate. Every hook in `router.ts` has one.
- Every `engines` example is a lower bound (`">=2.1.400"`). No page says to cap it from above.

### Versions and updates

- "Claude Code computes a version for every plugin it installs, and that version is how it detects an update. `claude plugin update` and background auto-update compute the version again and don't replace the cached copy when it matches what `installed_plugins.json` records." The manifest's `version` "comes first". "A manifest that pins `"version": "1.0.0"` keeps every user on the cached copy until its author changes the string, however many commits they push." "The version also names the plugin's cache directory." `[loading]` Versions and updates.
- With no `version`, a relative path inside a Git-hosted marketplace gets "The commit SHA of the installed directory" `[loading]` How Claude Code computes the version.
- "Users get a new copy only when the plugin's computed version differs from the one they have." "Background auto-update is off for your marketplace by default, and `marketplace.json` has no field to turn it on." `[host]` Keep users up to date.
- "If you set `version` in `plugin.json` and later push commits without changing it, `claude plugin update` prints `<name> is already at the latest version (1.0.0).` and users keep the old copy. Either increment `version` on every release, or omit it in a git-hosted marketplace so Claude Code uses the commit SHA instead." And for CI: "keep `--strict`, which also fails the run with exit code 1 on warnings such as an unknown manifest field or a missing `version`. Drop `--strict` if you chose to omit `version`" `[publish]` Prepare your plugin for release.
- Applied to our history: an install made between 3f036a1 and da1e68c holds a 2026-11-23 copy recorded as `0.1.2`. On 2026-11-24 `main` was also `0.1.2`, so `claude plugin update` reports it current and the user keeps the old copy. An install made after 9e247d3 lacks c694a43's skill edits for the same reason. This applies the quoted rule to our git history; no real install was tested.
- Tags: "Tag the release in git when other plugins declare a version range on yours, because those ranges resolve against tags." `claude plugin tag` "creates a `{name}--v{version}` tag" `[publish]` Tag a release. No plugin depends on ours, so the `v<version>` tags in `RELEASING.md` break nothing.
- "When a copied plugin updates mid-session, hook commands, monitors, MCP servers, and LSP servers keep using the previous version's path. Run `/reload-plugins` to switch hooks, MCP servers, and LSP servers to the new path." `[loading]` When auto-update runs.

### Data across updates and uninstall

- `${CLAUDE_PLUGIN_DATA}` is "`~/.claude/plugins/data/<id>/`, created on first reference and kept across plugin updates", and "`${CLAUDE_PLUGIN_ROOT}` changes when the plugin updates, so don't write state there." `[manifest]` Environment variables.
- On update, Claude Code "writes an `.orphaned_at` marker into the previous version directory. It removes that directory in a background cleanup 14 days later, so a session that already loaded the old version keeps running." `[loading]` Cleanup of previous versions. So an old and a new version can share one data folder at the same time. `store.py`'s rule that a file from a newer schema is read and never written is what keeps the old session from overwriting the new layout. The docs say nothing about migrations themselves.
- The only update-time pattern the docs show is a SessionStart hook that diffs `package.json` against a copy kept in the data folder `[components]` Install dependencies into the data directory.
- "When you uninstall a plugin from the last scope it's installed at, Claude Code also deletes the plugin's stored options and secrets and its data directory", unless `--keep-data` `[cli]` plugin uninstall. The README says so.

### claude plugin validate

- "`claude plugin validate` is the authoritative check for a manifest." "Pass `--strict` to turn warnings into failures in CI" `[manifest]` Validate the manifest.
- A directory with only `.claude-plugin/marketplace.json` is validated as a marketplace, and "from a marketplace directory, Claude Code doesn't open the skill, agent, or command files of plugins the marketplace lists in other directories ... To find errors in those files, validate each plugin directory" `[cli]` Validate a directory. CI validates both, as the page asks.
- `hosts` in `--json` lists "`named` hosts, plus `unread` plugin parts that could reach others ... A plugin can reach unlisted hosts." `[cli]` Output and exit codes.

## Verdict

Partly right. Every field in `plugin.json`, `marketplace.json` and `hooks/hooks.json` is documented, spelled as the references spell it, and used the way they describe, and nothing we set is deprecated. The `>=2.1.329` floor is the lowest build with every field and call we use, the mod fails open where a call is missing, and the data folder with its schema rule fits what `[loading]` says about two versions running at once. The update flow is wrong: `[loading]`, `[host]` and `[publish]` say users get a new copy only when the version string changes, and `main` has reused `0.1.0` and `0.1.2` and shipped four plugin changes with no new version, so some installs can't reach the current code through `claude plugin update`.

## What to change

1. Give every change that reaches `main` under `plugins/model-router/` a version string `main` has never carried. The next one is `0.1.3` or higher, never `0.1.0`, `0.1.1` or `0.1.2` again. Touches `plugin.json` `version` and `RELEASING.md` step 1, which should say that a merge to `main` that changes plugin files without a new version never reaches existing installs, and that a version string is never reused. Deletes nothing. Required by `[loading]` Versions and updates, `[host]` Release a new version and `[publish]` Decide how you'll version. Leaving `version` out so the commit SHA is the version is the documented alternative, but it means dropping `--strict` `[publish]`, so keep `version`.
2. Add a CI step that fails a pull request touching `plugins/model-router/` when `plugin.json` `version` equals the base branch's or an existing `v*` tag. Leaving `plugins/model-router/README.md` out of the check is a fair cut, since it doesn't change behavior. Touches `.github/workflows/ci.yml`, one step. Deletes nothing. Judgment, backed by the same pages as item 1.
3. Run `claude plugin validate --strict` once a day on the newest Claude Code release in `.github/workflows/upstream-watch.yml`, and set the `ci.yml` validate matrix to the floor plus the newest tested build (`2.1.331`, which the README names and `RELEASING.md` step 3 already asks for). A new release can add a validate warning or change the mods API, and this shows it before users hit it. Touches `upstream-watch.yml` and `ci.yml`. Deletes nothing. Judgment, backed by `[mods-create]`, `[mod-types]` and `[changelog]` 2.1.323.
4. Delete `plugins[0].description` from `.claude-plugin/marketplace.json`, so `/plugin` shows the one in `plugin.json` (a relative-path entry's `plugin.json` is read before install), and cut `plugin.json` `description` to one or two short sentences. Touches both files. Deletes the second description. Judgment, backed by `[marketplace]` Display fields and `[manifest]` Fields ("Short explanation").
5. Add one sentence to the requirements in `README.md`, `plugins/model-router/README.md` and `docs/GUIDE.md`: Claude Code 2.1.271 to 2.1.322 doesn't read the floor and loads the plugin anyway, so update Claude Code first. A runtime check with `$.session.version()` in `router.ts` is the bigger option, and it isn't needed while the mod already does nothing below 2.1.326. Deletes nothing. Judgment, backed by `[manifest]` (the `engines` row, Unrecognized fields, and the `options` version note).
6. Fix the "Plugin packaging" rows in `docs/CLAUDE_CODE_DOCS.md`: cite the manifest reference's "Environment variables" section for `${CLAUDE_PLUGIN_DATA}` (the row points at "User configuration", which doesn't cover it), and state that `engines` is enforced only from 2.1.323 and that `network` "doesn't allow or block any host". Deletes nothing. Judgment, backed by `[manifest]`.

## What the sources do not say

- What a user on a build outside `engines` sees, or how "install and update pick the newest version that fits" finds an older copy of a relative-path plugin. Only the 2.1.323 changelog line says it, and no page says whether it reads git tags, or in which form.
- Whether to cap `engines` from above. Not documented.
- What builds 2.1.271 to 2.1.322 do with this plugin. The range is my reading of two quotes, not a stated rule, and it was not run.
- Any migration hook, update event or guidance for a plugin's data between versions. The docs give only "kept across plugin updates" and the 14-day old copy.
- What happens to a saved option when a release renames or removes its key, or drops a value from `options`. `settings.py` falls back to the default for a value it doesn't know, by its own choice.
- Whether `${user_config.KEY}` in skill content takes the manifest `default` when the user never set the option. That decides whether skills could read options directly, in place of `settings.record`'s copy. The copy is written only at SessionStart, and whether a `/config` change fires SessionStart is not documented either.
- Whether a hooks module switches to the new version on `/reload-plugins` or a mid-session update. The loading page names hooks, monitors, MCP and LSP servers only.
- Which build's types `claude plugin validate` checks a `.ts` hooks module against.
- Whether `sh` resolves as an exec-form `command` on Windows when Git Bash isn't on `PATH`. The hooks page says exec form needs a real executable, and the README lists `sh` as a requirement.
- The full rule for marketplace names that "impersonate" an official one. The page gives two examples. `claude-code-model-router` is not on the reserved list, and CI's validate accepts it (not re-run here).
- Whether Claude Code ever removes a folder under `~/.claude/plugins/data/` that no installed plugin owns, such as the `model-router-local` fallback in `store.data_dir`.
- Whether `/plugin` search reads `plugin.json` `keywords` or only the entry's `tags`, so both stay.
