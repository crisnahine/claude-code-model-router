SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
# Minor units per currency, where it isn't 2.
DECIMALS = {"JPY": 0}
ZERO_DECIMAL = {code for code, places in DECIMALS.items() if places == 0}
