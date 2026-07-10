
def convert_price(amount_nok: float, target_currency: str) -> float:
    """Simple conversion using static rates. Replace with external API call."""
    rates = {
        "NOK": 1.0,
        "USD": 0.09,
        "EUR": 0.085,
        "RUB": 8.5
    }
    if target_currency not in rates:
        raise ValueError("Unsupported currency")
    return amount_nok * rates[target_currency]
