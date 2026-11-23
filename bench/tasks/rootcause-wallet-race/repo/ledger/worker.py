"""One payout worker: takes jobs off a queue and applies them to the shared store."""
from ledger import wallet


def run(store, jobs, log=print):
    for job in jobs:
        kind, *args = job
        log(f"{kind} {args} start")
        try:
            getattr(wallet, kind)(store, *args)
        except wallet.InsufficientFunds as e:
            log(f"{kind} {args} refused: {e}")
        else:
            log(f"{kind} {args} done")
