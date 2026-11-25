def format_cents(cents, currency="USD"):
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d} {currency}"
