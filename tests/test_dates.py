"""Milestone 3: relative dates are resolved by code against the dataset, not today (B9, B10)."""

import pytest

from portfolioiq.dates import DateCatalog, interpret_dates


@pytest.fixture(scope="module")
def catalog(readonly_db):
    return DateCatalog.from_db(readonly_db)


def only(question, catalog):
    found = interpret_dates(question, catalog)
    assert len(found) == 1, found
    return found[0]


def test_latest_dates_are_read_per_table(catalog):
    assert catalog.latest("transactions", "transaction_date") == "2023-10-09"
    assert catalog.latest("historical_prices", "price_date") == "2023-10-09"
    assert catalog.latest("portfolio_performance", "performance_date") == "2023-10-09"
    assert catalog.latest("risk_metrics", "calculation_date") == "2023-10-09"
    assert catalog.latest("holdings", "purchase_date") == "2023-09-25"  # not every table ends on 9 Oct
    assert catalog.latest("transactions", "settlement_date") == "2023-10-11"
    assert catalog.dataset_anchor == "2023-10-09"


@pytest.mark.parametrize("question, start, end, rule", [
    ("How many trades happened in the past 7 days?", "2023-10-03", "2023-10-09", "rolling 7 days"),
    ("Transactions in the last 30 days", "2023-09-10", "2023-10-09", "rolling 30 days"),
    ("Show transactions over the past 90 days", "2023-07-12", "2023-10-09", "rolling 90 days"),
    ("Transactions in the past 12 months", "2022-10-10", "2023-10-09", "rolling 12 months"),
    ("Transactions in the past week", "2023-10-03", "2023-10-09", "rolling 1 week"),
    ("Transactions last week", "2023-10-02", "2023-10-08", "previous calendar week"),
    ("Transactions last month", "2023-09-01", "2023-09-30", "previous calendar month"),
    ("Transactions last quarter", "2023-07-01", "2023-09-30", "previous calendar quarter"),
    ("Show all transactions from last year", "2022-01-01", "2022-12-31", "previous calendar year"),
    ("Transactions this month", "2023-10-01", "2023-10-09", "month to date"),
    ("Year to date transactions", "2023-01-01", "2023-10-09", "year to date"),
    ("Transaction count YTD", "2023-01-01", "2023-10-09", "year to date"),
    ("Transactions today", "2023-10-09", "2023-10-09", "the anchor date"),
    ("Transactions yesterday", "2023-10-08", "2023-10-08", "the day before"),
])
def test_relative_phrases_resolve_to_exact_dates(catalog, question, start, end, rule):
    found = only(question, catalog)
    assert (found.start, found.end) == (start, end)
    assert rule in found.rule
    assert (found.table, found.column, found.anchor_date) == ("transactions", "transaction_date", "2023-10-09")


def test_anchor_follows_the_table_the_question_is_about(catalog):
    found = only("Which holdings were purchased last month?", catalog)
    assert (found.column, found.anchor_date) == ("purchase_date", "2023-09-25")
    assert (found.start, found.end) == ("2023-08-01", "2023-08-31")


def test_without_a_table_hint_the_dataset_anchor_is_used(catalog):
    found = only("What happened in the past 7 days?", catalog)
    assert (found.table, found.anchor_date) == (None, "2023-10-09")
    assert found.anchor_source == "latest date across the time-series tables"


def test_disclosure_says_it_is_not_today(catalog):
    text = only("Sharpe ratio over the past 7 days", catalog).describe()
    assert "2023-10-03 to 2023-10-09" in text and "not today's date" in text
    assert "portfolio_performance.performance_date" in text


def test_questions_without_relative_dates_resolve_nothing(catalog):
    assert interpret_dates("Which portfolios hold NVIDIA?", catalog) == []
    assert interpret_dates("Show me the last 5 transactions", catalog) == []  # a count, not a period


def test_month_arithmetic_clamps_to_month_end(catalog):
    found = only("Transactions in the past 1 month", catalog)
    assert (found.start, found.end) == ("2023-09-10", "2023-10-09")
