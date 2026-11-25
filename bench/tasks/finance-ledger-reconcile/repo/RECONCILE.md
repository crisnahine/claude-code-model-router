# October 2026 cash reconciliation, operating account

We reconcile the October bank statement for the operating account (`bank.csv`) against the general ledger's cash account (`gl.csv`). Both files are exports with amounts in US dollars: money in is positive, money out is negative.

`bank.csv` holds every bank movement with a value date in October 2026. `gl.csv` is a wider export: it holds every posting to account 1010 from 25 September to 7 November 2026, so it also carries postings that belong to the September and November statements.

## How rows match

- A bank row and a GL row match on `ref`. The bank prints the same reference we put on the payment or invoice.
- One bank movement can be booked as several GL rows with the same `ref`, for example a receipt split across two invoices or cost centres. Compare the bank amount with the sum of those GL rows.
- A difference of 0.50 or less is rounding and is not a mismatch. Anything above 0.50 is.
- The GL may post a movement a few days before or after the bank's value date. That is fine as long as both dates fall in the same calendar month.
- Foreign payments go out by wire. The bank's description shows the amount in the foreign currency and the rate it used. The GL must book the US dollar amount the bank charged us. A small difference within the 0.50 rounding allowance is fine.
- A GL row from outside October with no bank row belongs to another statement. It is not a mismatch here.

## Kinds of mismatch

Report every `ref` that does not reconcile, once, with one of these kinds:

- `missing`: the ref is on the October bank statement but nowhere in the GL, or it is posted in the GL in October but not on the bank statement.
- `duplicate`: the GL rows for the ref add up to more than the bank amount, and a single one of them already matches the bank amount. The same movement was booked twice.
- `period`: the amounts agree, but the GL posted the movement in a different month from the bank's value date.
- `currency`: a foreign payment where the GL booked the foreign-currency figure instead of the dollar amount.
- `sign`: the GL amount is the bank amount with the sign reversed.
- `amount`: the amounts differ by more than 0.50 and none of the kinds above explains it.

If more than one kind could describe a ref, use the first one in this list.
