# Changelog

## 2.4.0

- Backups keep links as links, so deploy folders with a `current` link restore as they were. Before, a restore
  copied the target's contents in place of each link.
- `safe_extract` checks every member before it writes anything, and a refused archive leaves the destination as
  it was. The installer and the restore rely on that to clean up.

## 2.3.0

- `safe_extract` refuses a link whose target is absolute or climbs out of the destination, after a bundle on
  the staging marketplace shipped `config -> ../../../etc`.
- Size limits on archives: 20,000 members, 256 MB a file, 1 GB in all.

## 2.2.0

- The plugin registry lists only folders that hold an install marker, so a half-finished install no longer
  shows up as a plugin.
- `python3 -m plugins pack` builds a bundle from a plugin's folder.
