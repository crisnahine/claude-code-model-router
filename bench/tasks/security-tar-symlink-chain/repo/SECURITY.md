# Security model

## Archives

The app unpacks tar archives from two sources, and trusts neither:

- **Plugin bundles** (`plugins/installer.py`). Anyone with a marketplace account can upload one. The bundle is
  installed into the plugins folder on every app server, and the app later imports the bundle's entry file.
- **Backups** (`backups/restore.py`). A user can upload any tar as a backup of their workspace; nothing proves
  the app made it. The restore runs on the shared workers, next to every other user's workspace.

The app servers and the shared workers run Linux, on case-sensitive filesystems.

Both go through `archive.extract.safe_extract`, and nothing else in the app may unpack an archive. Its promise:

1. Nothing is created, changed or removed outside the destination folder.
2. No link left in the destination points outside it, so nothing that later reads the extracted files can be led
   out of it.
3. An archive that breaks either rule is refused with `UnsafeArchive` before anything is written, so the
   callers can treat a refused archive as one that never happened.

Normal archives must still extract as they are: plugin bundles ship links to their own assets, and backups of
deploy folders hold `current` links, links into them and links to files later in the archive.

## Reporting

Write to security@ with the steps to reproduce. Please don't open a public issue.
