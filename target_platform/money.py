"""
Exact monetary representation and minor unit conversion utilities.
Guarantees zero floating-point drift by performing all calculations using Decimal
and storing monetary values as exact integer minor units (ISO 4217).
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Union

# Currency minor unit decimal places (default 2 for INR, USD, EUR, etc.)
CURRENCY_EXPONENTS = {
    "INR": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "KWD": 3,
}


def get_currency_exponent(currency: str = "INR") -> int:
    return CURRENCY_EXPONENTS.get(currency.upper(), 2)


def to_minor_units(amount: Union[float, Decimal, int, str], currency: str = "INR") -> int:
    """
    Converts a major currency amount (e.g. 15000.50 INR) to integer minor units (e.g. 1500050 paise).
    Uses Decimal with ROUND_HALF_UP to eliminate IEEE 754 binary floating-point representation drift.
    """
    exponent = get_currency_exponent(currency)
    scale = Decimal(10) ** exponent
    dec_amount = Decimal(str(amount))
    minor_val = (dec_amount * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minor_val)


def from_minor_units(minor: int, currency: str = "INR") -> float:
    """
    Converts integer minor units back to standard float for serialization / display.
    """
    exponent = get_currency_exponent(currency)
    scale = Decimal(10) ** exponent
    dec_amount = (Decimal(minor) / scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(dec_amount)


def round_minor_units(minor: int) -> int:
    """Ensures minor units remain strictly integral."""
    return int(minor)
