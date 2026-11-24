# ledger

The ledger moves from the old service's v2 exports to its own v3 files this release. The full export is about
40 million lines; `data/` holds a small sample from it.

```bash
python3 -m ledger migrate data/export_v2.jsonl data/accounts.json /tmp/ledger_v3.jsonl
python3 -m ledger reconcile /tmp/ledger_v3.jsonl data/control_totals.json
python3 -m ledger statement /tmp/ledger_v3.jsonl acc-ny USD
```

| File | What it is |
|---|---|
| `docs/FORMAT_V2.md` | the v2 export, and what older exporters did differently |
| `docs/FORMAT_V3.md` | the v3 file, and how a v2 export migrates to it |
| `data/accounts.json` | each account's name, currency and UTC offset |
| `data/export_v2.jsonl` | a sample export |
| `data/expected_v3.jsonl` | what the sample migrates to |
| `data/control_totals.json` | finance's closing balance for each account in the sample |

`control_totals.json` comes from finance's own books, not from this code, so a migration that reconciles against
it got the money right.

## Tests

```bash
python3 -m unittest test_formats test_migrate
```
