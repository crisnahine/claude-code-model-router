# Ledger file, v3

The ledger's own format from this release on. One JSON object per line, UTF-8, each line written by
`ledger.v3.dump_line`: compact, keys sorted, non-ASCII characters kept as they are, a newline after every line.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | the entry's id; each id appears once in a file |
| `account` | string | the account id |
| `type` | string | `credit`, `debit`, `fee`, `refund` or `chargeback` |
| `amount_minor` | integer | the amount in whole minor units of `currency`, positive for money in, negative for money out |
| `currency` | string | the ISO 4217 code, never null |
| `posted_at` | string | when the entry was posted, in UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| `memo` | string | free text, may be empty |
| `source_rev` | integer | the revision of the v2 line it came from |

Minor units follow the currency: two decimal places for most currencies, none for JPY, three for KWD.
`ledger/money.py` has the table.

Records are sorted by `account`, then `posted_at`, then `id`, all compared as strings. `ledger.v3.write` refuses
records in any other order.

## Migrating from v2

Every entry in a v2 export becomes one v3 record: its current revision, as the v2 format's history explains which
line that is. Each field of that line carries over to the field above that means the same thing. `type` is always
one of the five names, and `amount_minor` carries the sign the type gives it. `currency` and `posted_at` are filled
in where v2 left them to be inferred.

A line that can't be migrated (an unknown kind, an account the accounts file doesn't have, an amount with more
decimal places than its currency allows) stops the migration with a `MigrationError` that names the line.
Nothing is written then.

## Example

```json
{"account":"acc-ny","amount_minor":-1230,"currency":"USD","id":"t-1001","memo":"coffee","posted_at":"2023-03-04T15:00:00Z","source_rev":1,"type":"debit"}
```
