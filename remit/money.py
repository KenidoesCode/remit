"""Money is paise. Integers only. Never float, never Decimal-in-JSON.

Every amount that crosses a boundary in REMIT is an int of paise. Rupee
formatting happens exactly once, at the UI edge.
"""
from __future__ import annotations

Paise = int


def rupees(paise: Paise) -> str:
    sign = "-" if paise < 0 else ""
    p = abs(int(paise))
    return f"{sign}\u20b9{p // 100:,}.{p % 100:02d}"


def to_paise(rupee_value: float) -> Paise:
    """Only for fixtures/tests. Production amounts come from the catalog."""
    return int(round(rupee_value * 100))
