"""Milestone 1: the supplied reference SQL (Q1-Q8) returns the documented results.

This checks the reference database, not the agent. Only test and evaluation code
may read the ground-truth file; the agent's runtime code never does.
"""

import json

import pytest

from portfolioiq.config import PROJECT_ROOT

GROUND_TRUTH = PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json"
REFERENCE_SQL = {
    q["id"]: q["ground_truth"]["sql_query"]
    for q in json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["questions"]
    if "sql_query" in q["ground_truth"]
}

ACTIVE_PORTFOLIOS = {
    "Growth Equity Fund",
    "Conservative Income Fund",
    "Tech Innovation Fund",
    "Balanced Portfolio",
    "ESG Sustainable Fund",
    "Small Cap Value Fund",
    "International Equity Fund",
    "Fixed Income Plus",
    "Dividend Aristocrats Fund",
    "Emerging Markets Fund",
}
TECHNOLOGY_SYMBOLS = {"AAPL", "MSFT", "GOOGL", "META", "NVDA", "AVGO", "CRM", "ORCL"}


def run(db, question_id):
    return db.execute(REFERENCE_SQL[question_id]).fetchall()


def test_reference_sql_exists_for_q1_to_q8():
    assert sorted(REFERENCE_SQL) == list(range(1, 9))


def test_q1_portfolio_count(db):
    assert run(db, 1) == [(13,)]


def test_q2_active_portfolio_names(db):
    rows = run(db, 2)
    assert len(rows) == 10
    assert {r[0] for r in rows} == ACTIVE_PORTFOLIOS


def test_q3_technology_securities(db):
    rows = run(db, 3)
    assert len(rows) == 8
    assert {r[0] for r in rows} == TECHNOLOGY_SYMBOLS


def test_q4_high_risk_aum(db):
    assert run(db, 4) == [(pytest.approx(85_000_000.0),)]


def test_q5_top_five_by_cost_basis_in_order(db):
    rows = run(db, 5)
    assert [(r[1], r[4]) for r in rows] == [
        ("AAPL", 438_000.0),
        ("AMZN", 320_000.0),
        ("MSFT", 304_400.0),
        ("NVDA", 288_040.0),
        ("CRM", 185_400.0),
    ]


def test_q6_only_the_seven_populated_sectors(db):
    rows = run(db, 6)
    assert len(rows) == 7
    assert rows[0] == ("Consumer Discretionary", pytest.approx(3380.0))
    assert {"Industrials", "Real Estate", "Utilities"}.isdisjoint(r[0] for r in rows)


def test_q7_technology_share_by_market_value(db):
    shares = {r[0]: r[3] for r in run(db, 7)}
    assert len(shares) == 8
    assert shares["Tech Innovation Fund"] == pytest.approx(100.0)
    assert shares["Growth Equity Fund"] == pytest.approx(84.67)
    assert shares["International Equity Fund"] == pytest.approx(47.2)


def test_q8_only_one_portfolio_spans_more_than_five_sectors(db):
    assert run(db, 8) == [("Total Stock Market Index Fund", 6, 12, pytest.approx(0.5))]
