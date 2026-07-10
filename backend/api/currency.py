
from fastapi import APIRouter

router = APIRouter()

# Example currency rates. In a real-world scenario, integrate with an FX service.
RATES = {
    "NOK": 1.0,
    "USD": 0.09,
    "EUR": 0.085,
    "RUB": 8.5
}

@router.get("/")
def get_rates():
    return RATES
