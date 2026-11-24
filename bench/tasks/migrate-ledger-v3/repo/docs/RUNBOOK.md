# Cutover to v3

The steps for the night the ledger stops reading the old service's exports. Each step has to finish before the
next one starts.

1. Freeze writes on the old service. Its export job runs one last time at 01:00 UTC and writes
   `export_v2_final.jsonl` to the transfer bucket.
2. Copy the final export and `accounts.json` from the transfer bucket to the migration host.
3. Migrate:

   ```bash
   python3 -m ledger migrate export_v2_final.jsonl accounts.json ledger_v3.jsonl
   ```

   A `MigrationError` names the v2 line it stopped on. Fix the cause in the migration, not in the export: the
   export is the record of what the old service held.
4. Reconcile against finance's control totals for the same cut-off:

   ```bash
   python3 -m ledger reconcile ledger_v3.jsonl control_totals.json
   ```

   Every account must reconcile. A difference of exactly one entry's amount usually means a duplicate kept twice
   or a revision lost; a difference of a factor of 100 or 1000 means minor units in the wrong currency.
5. Spot-check five statements against the old service's web view, one per currency:

   ```bash
   python3 -m ledger statement ledger_v3.jsonl acc-ny USD
   ```

   Times in the statement are UTC. The old web view showed local time.
6. Upload `ledger_v3.jsonl` and switch the ledger's source setting to `v3`.

## Rolling back

Until step 6 nothing has changed for users: stop, and unfreeze the old service. After step 6, switch the source
setting back to `v2` and unfreeze; entries written to v3 in between have to be replayed by hand.
