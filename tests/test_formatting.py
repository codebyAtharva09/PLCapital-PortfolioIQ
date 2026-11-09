"""Milestone 2: display formatting rounds half up and never hides missing values."""

import pytest

from portfolioiq.formatting import MINUS, NOT_AVAILABLE, pct


@pytest.mark.parametrize("fraction, expected", [
    (0.2211, "22.11"),
    (0.22, "22.00"),
    (1.0, "100.00"),
    (0.15625, "15.63"),  # half up, not Python's half-even (which gives 15.62)
    (0.46875, "46.88"),
    (0.6111111111111112, "61.11"),
    (0.0, "0.00"),
    (-0.02, f"{MINUS}2.00"),
    (-0.000001, "0.00"),  # rounds to zero: no negative zero
    (12.5, "1,250.00"),
])
def test_pct(fraction, expected):
    assert pct(fraction) == expected


def test_missing_value_is_shown_as_not_available():
    assert pct(None) == NOT_AVAILABLE
