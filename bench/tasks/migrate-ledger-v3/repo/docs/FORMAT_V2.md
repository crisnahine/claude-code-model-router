# Ledger export, v2

The format the old ledger service exports, one JSON object per line, UTF-8. It has been written by three
generations of exporter since 2019, and every file we hold mixes lines from all of them.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | the entry's id, unique per entry |
| `rev` | integer from 1 | the entry's revision; each edit raises it by one |
| `account` | string | the account id, as in the accounts file |
| `kind` | string | what the entry is: `credit`, `debit`, `fee`, `refund` or `chargeback` |
| `amount` | string | a positive decimal, in the entry's currency |
| `currency` | string or null | the ISO 4217 code |
| `ts` | string | when the entry was posted, ISO 8601 |
| `memo` | string | free text, may be empty |
| `exported_at` | string | when this line was written; means nothing to the ledger |

`amount` is always positive. Whether money came in or went out is the `kind`: credits and refunds come in,
debits, fees and chargebacks go out.

## History

What the older exporters did differently. All of it is still in the files.

- **2019 to March 2021.** `kind` was a two-letter code, not a name. `ledger/kinds.py` lists the codes.
- **2020.** The bulk importer that loaded the 2020 backlog wrote `ts` without an offset. Those times are the
  account's local time, at the `utc_offset` the accounts file gives the account.
- **Until 2022.** `currency` was optional, and exporters wrote `null` for an entry in the account's own currency.
  A non-null currency is the entry's own, even when it differs from the account's.
- **Always.** The exporter retries a batch when the upload times out, so a line can appear more than once. An
  entry edited between two attempts appears with both revisions; the one with the highest `rev` is current. An
  edit that landed during a retry can also repeat a `rev` with a newer memo, so between lines with the same `id`
  and `rev`, the later line in the file is the newer one.

## Example

```json
{"id": "t-1001", "rev": 1, "account": "acc-ny", "kind": "debit", "amount": "12.30", "currency": "USD", "ts": "2023-03-04T10:00:00-05:00", "memo": "coffee", "exported_at": "2023-03-05T01:00:00Z"}
```
