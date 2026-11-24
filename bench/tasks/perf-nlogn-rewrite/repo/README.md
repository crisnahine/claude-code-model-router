# reports

The monthly sales report finance reads on the first working day of each month.

```bash
python3 -m reports sales.csv regions.csv                  # print it
python3 -m reports sales.csv regions.csv --out report.txt
python3 -m reports sales.csv regions.csv --standings standings.csv
python3 -m reports sales.csv regions.csv --verify         # refuse a report whose totals disagree
```

`--verify` reads the report back and checks that the region totals, the last running total in Days and the
customer standings add up to the same amount.

From Python: `reports.render(sales_path, regions_path)` returns the report as one string.

## Inputs

`sales.csv` is the point-of-sale export: a header line `txn_id,date,region,customer,product,amount`, then one
line per sale. Fields are trimmed. A row is rejected, counted and left out when it has the wrong number of fields,
an empty txn_id, customer or product, a date that isn't a real `YYYY-MM-DD` day, a region code the regions file
doesn't list, or an amount that isn't a plain decimal with at most two places (`12`, `12.5` and `-3.75` are
fine; `1e3`, `.5`, `+2` and `1,000` are not). Negative amounts are refunds.

The tills retry a sale when the network drops, so the same txn_id can come again. The first kept row with a
txn_id counts; later rows with it are duplicates, whatever they hold. A rejected row doesn't claim its txn_id.

`regions.csv` lists the regions as `code,name`, in the order the report prints them.

## People and products

Names are compared the way people read them: Unicode NFC, whitespace runs made one space, and case folded, so
`Ana Lima`, ` ana  lima` and `ANA LIMA` are one customer. The report shows a customer or a product under the
spelling of its first kept sale.

## The report

1. A header: rows read, kept, duplicates and rejected, and the first and last date kept.
2. One section per region, in the regions file's order, even for a region with no sales: count, total, the
   average sale rounded to the cent (halves away from zero), the top three customers and every product.
3. Days: each day with sales, its count and total, and the running total.
4. Customer standings: every customer by total spent.

Ranks are competition ranks: equal totals share a rank and the next rank skips (1, 2, 2, 4). The top three
customers are everyone ranked 1 to 3, so a tie for third shows all of them. Equal totals are listed by name,
ignoring case. Products are listed by total, biggest first, then by name.

Money is written with a thousands separator and two decimals: `-1,234.50`.

## Tests

```bash
python3 -m unittest test_report
python3 make_sales.py /tmp/sales.csv 200000    # a month at full size
```
