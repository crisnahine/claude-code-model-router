# workspace-host

The parts of the workspace host that handle archives.

| Package | What it does |
|---|---|
| `archive/` | `safe_extract`, the app's only way to unpack a tar, plus its path rules and size limits |
| `plugins/` | installs marketplace plugin bundles (`install_bundle`) and lists them (`registry`) |
| `backups/` | makes a backup of a user's workspace (`create_backup`) and restores an uploaded one (`restore_backup`) |

A plugin bundle is a tar with `plugin.json` at its root:

```json
{"name": "hello", "version": "1.2.0", "entry": "main.py", "description": "Says hello"}
```

A backup is a tar of the workspace folder, links kept as links, with `MANIFEST.json` at its root listing each
file's sha256 and each link's target. A restore checks the files against it.

`SECURITY.md` says what `safe_extract` promises and why.

## Tests

```bash
python3 -m unittest test_extract
```
