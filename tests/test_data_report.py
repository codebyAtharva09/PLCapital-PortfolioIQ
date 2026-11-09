"""Milestone 1: the data-findings report states the key facts, computed from the data."""

from portfolioiq.data_report import build_report


def test_report_contains_the_key_findings(db):
    report = build_report(db)
    expected = [
        "holdings total 98.00%; 2.00% unattributed",  # portfolio 11
        "mixed: Methods A and B differ",  # e.g. Balanced Portfolio
        "no stocks: sector exposure not applicable",  # bond-only funds
        "| International Equity Fund | 36.00% | 47.20% |",  # weight basis disagreement
        "23 positions have recorded transactions, and 1 of them reconciles",
        "reconciliation gaps, not proven data errors",
        "portfolios 1, 2, 3, 4",  # performance/risk coverage
        "**2023-10-09**",  # as-of date
        "`US` (30)",  # every security marked US
        "Industrials, Real Estate, Utilities",  # sectors with no securities
        "Home Depot (HD) is classified as Consumer Staples",
    ]
    missing = [text for text in expected if text not in report]
    assert not missing, f"report is missing: {missing}"
