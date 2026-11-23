SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
# Minor units per currency, where it isn't 2.
DECIMALS = {"JPY": 0}


def decimals_for(code):
    return DECIMALS.get(code, 2)
