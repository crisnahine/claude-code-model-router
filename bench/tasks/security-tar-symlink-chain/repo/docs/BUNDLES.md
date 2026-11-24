# Plugin bundles

For people writing plugins for the marketplace.

## Layout

A bundle is a tar archive (plain or gzip) of your plugin's folder. At its root:

- `plugin.json`, the manifest: `name` (lowercase letters, digits and `-`, starting with a letter), `version`
  (`MAJOR.MINOR.PATCH`), `entry` (the Python file the app imports) and an optional `description`.
- The entry file, which defines `setup(app)`. The app calls it once when it loads the plugin.
- Anything else the plugin needs: templates, assets, data files.

Build one with:

```bash
python3 -m plugins pack my-plugin/ my-plugin-1.0.0.tar
```

## What a bundle may hold

Regular files, folders and symbolic links. A link must point to something inside the bundle: links between your
own assets are fine (`logo.svg -> assets/logo-2024.svg`), a link to `/etc/hosts` or to `../../` is not.
Hard links, devices and FIFOs are refused. So is a name listed twice.

Limits: 20,000 members, 256 MB per file, 1 GB in all.

A refused bundle installs nothing. The upload page shows the reason, which names the member it stopped on.

## Versions

Install a new version by removing the old one first:

```bash
python3 -m plugins remove plugins/ my-plugin
python3 -m plugins install my-plugin-1.1.0.tar plugins/
```
