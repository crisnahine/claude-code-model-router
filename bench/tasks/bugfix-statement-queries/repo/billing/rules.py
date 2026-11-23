def counts(status: str, method: str) -> bool:
    """A settled payment counts. A pending one counts only when it was made by card. Refunds are settled payments with a negative amount."""
    return status == "settled" or (status == "pending" and method == "card")
