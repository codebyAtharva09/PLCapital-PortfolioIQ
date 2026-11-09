"""Display formatting shared by the CLI and the Streamlit app (docs/DESIGN_SYSTEM.md, section 4).

Calculations keep full precision; rounding happens only here, at display time.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MINUS = "−"  # true minus sign
NOT_AVAILABLE = "—"  # em dash


def pct(fraction: float | None, places: int = 2) -> str:
    """A fraction as a percentage figure without the % sign: 0.2211 -> "22.11".

    Rounds half up (0.15625 -> "15.63"), the convention in financial reporting, instead of
    Python's round-half-even.
    """
    if fraction is None:
        return NOT_AVAILABLE
    value = (Decimal(str(fraction)) * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{abs(value):,.{places}f}"
    return f"{MINUS}{text}" if value < 0 else text


def display_value(value) -> str:
    """A database value as shown in answers and tables. Values are shown as stored (rule D7): whole
    numbers get thousands separators, other numbers keep up to six decimals, NULL stays visible."""
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:,.0f}" if value.is_integer() else f"{value:,.6f}".rstrip("0").rstrip(".")
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}" if abs(value) >= 10_000 else str(value)
    return str(value)
