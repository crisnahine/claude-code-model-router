# Payout incident, nightly run

Six workers (p1 to p6) ran the night's payouts against the shared store, each in its own process. The morning reconcile found two problems. Log excerpts below, times are UTC.

## Reconcile

```
wallet        start   credits   transfers in   transfers out   expected   actual
merchant-17     400       +65              0               0        465      425
merchant-3     1200         0            +60             -15       1245     1245
merchant-9      800         0            +15             -60        755      755
```

merchant-17 is 40 cents short. Nobody withdrew from it. The other 12 wallets reconcile.

## Credits on merchant-17

```
02:14:07.412 p2 credit ['merchant-17', 40] start
02:14:07.412 p5 credit ['merchant-17', 25] start
02:14:07.413 p5 credit ['merchant-17', 25] done
02:14:07.413 p2 credit ['merchant-17', 40] done
```

## Workers that went quiet

The monitor shows p1 and p4 with no log line after 02:31:44 while p2, p3, p5 and p6 kept logging until 03:00 and exited normally. Last lines from each:

```
02:31:44.208 p1 transfer ['merchant-3', 'merchant-9', 15] start
02:31:44.209 p4 transfer ['merchant-9', 'merchant-3', 60] start
```

Neither line has a `done` or a `refused`. Both processes were still up at 06:00 and used no CPU. The two transfers were applied to the balances above only after we restarted p1 and p4 and replayed their queues.
