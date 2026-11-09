"""Milestone 2: the exposure engine follows rules B1 to B3 exactly.

Expected values are hand-derived from the CSV files and written out below. They are never
computed by calling the production code under test.
"""

import dataclasses
import json
import math
import sqlite3

import pytest

from portfolioiq import exposure
from portfolioiq.config import PROJECT_ROOT
from portfolioiq.data_notes import ALL_US_NOTE, SECURITY_NOTES
from portfolioiq.exposure import BONDS_LABEL, METHOD_A_DENOMINATOR, UNCLASSIFIED_LABEL, calculate_exposure
from portfolioiq.formatting import pct
from portfolioiq.verification import CheckStatus, VerificationStatus

# Method A (share of the whole portfolio) per sector, hand-derived from holdings.csv.
EXPECTED_METHOD_A = {
    "Growth Equity Fund": {"Technology": 0.93, "Consumer Discretionary": 0.07},
    "Conservative Income Fund": {"Financials": 0.15, "Healthcare": 0.12, "Consumer Staples": 0.05},
    "Tech Innovation Fund": {"Technology": 1.00},
    "Balanced Portfolio": {"Technology": 0.22, "Financials": 0.08, "Healthcare": 0.06},
    "ESG Sustainable Fund": {"Healthcare": 0.38, "Consumer Staples": 0.35, "Technology": 0.27},
    "Small Cap Value Fund": {"Healthcare": 0.63, "Financials": 0.37},
    "International Equity Fund": {"Energy": 0.38, "Technology": 0.36, "Automotive": 0.15, "Consumer Staples": 0.11},
    "Fixed Income Plus": {},
    "Dividend Aristocrats Fund": {"Consumer Staples": 0.70, "Healthcare": 0.30},
    "Emerging Markets Fund": {"Technology": 0.35, "Energy": 0.32, "Automotive": 0.18, "Consumer Discretionary": 0.15},
    "Total Stock Market Index Fund": {"Technology": 0.48, "Healthcare": 0.16, "Financials": 0.14,
                                      "Consumer Discretionary": 0.10, "Automotive": 0.06, "Consumer Staples": 0.04},
    "Total Bond Market Index Fund": {},
    "Total International Index Fund": {"Technology": 0.49, "Energy": 0.27, "Consumer Discretionary": 0.15,
                                       "Automotive": 0.09},
}
EXPECTED_BONDS = {"Conservative Income Fund": 0.68, "Balanced Portfolio": 0.64,
                  "Fixed Income Plus": 1.00, "Total Bond Market Index Fund": 1.00}
EXPECTED_TOTAL = {"Total Stock Market Index Fund": 0.98}  # every other portfolio totals 1.00
BOND_ONLY = ["Fixed Income Plus", "Total Bond Market Index Fund"]
SCHEMA = (PROJECT_ROOT / "data" / "database_schema.sql").read_text(encoding="utf-8")


def pid(db, name):
    return db.execute("SELECT portfolio_id FROM portfolios WHERE portfolio_name = ?", (name,)).fetchone()[0]


def check(result, name):
    return next(c for c in result.checks if c.name == name)


def synthetic_db(holdings, name="Test Fund", strategy="Growth"):
    """An in-memory database with the supplied schema and the given (symbol, type, sector, weight) rows."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    sectors = sorted({sector for _, _, sector, _ in holdings if sector})
    sector_ids = {sector: i for i, sector in enumerate(sectors, 1)}
    conn.executemany("INSERT INTO sectors (sector_id, sector_name) VALUES (?, ?)",
                     [(i, s) for s, i in sector_ids.items()])
    conn.execute("INSERT INTO portfolios (portfolio_id, portfolio_name, strategy_type) VALUES (1, ?, ?)",
                 (name, strategy))
    for i, (symbol, asset_type, sector, weight) in enumerate(holdings, 1):
        conn.execute("INSERT INTO securities (security_id, symbol, company_name, asset_type, sector_id, country) "
                     "VALUES (?, ?, ?, ?, ?, 'US')", (i, symbol, f"{symbol} Inc.", asset_type, sector_ids.get(sector)))
        conn.execute("INSERT INTO holdings (holding_id, portfolio_id, security_id, quantity, current_weight) "
                     "VALUES (?, 1, ?, 1, ?)", (i, i, weight))
    return conn


# --- every supplied portfolio -------------------------------------------------------------------

@pytest.mark.parametrize("name", list(EXPECTED_METHOD_A))
def test_every_portfolio_matches_hand_derived_values(db, name):
    result = calculate_exposure(db, pid(db, name))
    expected_a = EXPECTED_METHOD_A[name]
    equity = math.fsum(expected_a.values())
    by_sector = {s.sector: s for s in result.sectors}

    assert set(by_sector) == set(expected_a)
    for sector, value in expected_a.items():
        assert by_sector[sector].method_a == pytest.approx(value, abs=1e-12)
        assert by_sector[sector].method_b == pytest.approx(value / equity, abs=1e-12)
    assert result.eligible_stock_weight == pytest.approx(equity, abs=1e-12)
    bonds = next((e.weight for e in result.excluded if e.label == BONDS_LABEL), 0.0)
    assert bonds == pytest.approx(EXPECTED_BONDS.get(name, 0.0), abs=1e-12)
    total = EXPECTED_TOTAL.get(name, 1.0)
    assert result.total_weight == pytest.approx(total, abs=1e-12)
    assert result.residual == pytest.approx(1.0 - total, abs=1e-12)
    assert result.status == ("ok" if expected_a else "no_eligible_stocks")


@pytest.mark.parametrize("name", list(EXPECTED_METHOD_A))
def test_every_portfolio_is_independently_reconciled(db, name):
    result = calculate_exposure(db, pid(db, name))
    assert result.reconciliation.status is CheckStatus.PASS
    assert result.reconciliation.max_abs_difference <= 1e-12
    assert result.verification is VerificationStatus.INDEPENDENTLY_RECONCILED
    assert all(c.status is not CheckStatus.FAIL for c in result.checks)


@pytest.mark.parametrize("name", list(EXPECTED_METHOD_A))
def test_methods_reconcile_and_sectors_aggregate_their_holdings(db, name):
    result = calculate_exposure(db, pid(db, name))
    assert result.method_a_total == pytest.approx(1.0, abs=1e-12)  # sectors + excluded + residual
    if result.sectors:
        assert math.fsum(s.method_b for s in result.sectors) == pytest.approx(1.0, abs=1e-12)
    for sector in result.sectors:
        assert math.fsum(h.weight for h in sector.holdings) == pytest.approx(sector.method_a, abs=1e-12)
    methods = [s.method_a for s in result.sectors]
    assert methods == sorted(methods, reverse=True)  # largest first


# --- mixed, bond-only and residual cases --------------------------------------------------------

def test_mixed_portfolio_shows_both_denominators(db):
    result = calculate_exposure(db, pid(db, "Balanced Portfolio"))
    assert result.method_a_denominator == METHOD_A_DENOMINATOR
    assert result.method_b_denominator == "Eligible stock weight (36.00% of portfolio)"
    assert [(s.sector, pct(s.method_a), pct(s.method_b)) for s in result.sectors] == [
        ("Technology", "22.00", "61.11"), ("Financials", "8.00", "22.22"), ("Healthcare", "6.00", "16.67")]
    bonds = result.excluded[0]
    assert (bonds.label, pct(bonds.weight)) == (BONDS_LABEL, "64.00")
    assert [h.symbol for h in bonds.holdings] == ["UST-10Y", "UST-5Y", "UST-2Y", "CORP-AAA-5Y"]


def test_display_rounding_is_half_up(db):
    result = calculate_exposure(db, pid(db, "Conservative Income Fund"))
    # Method B = 0.15/0.32, 0.12/0.32, 0.05/0.32 = 46.875%, 37.5%, 15.625%
    assert [pct(s.method_b) for s in result.sectors] == ["46.88", "37.50", "15.63"]


@pytest.mark.parametrize("name", BOND_ONLY)
def test_bond_only_portfolio_has_no_sector_exposure(db, name):
    result = calculate_exposure(db, pid(db, name))
    assert result.status == "no_eligible_stocks"
    assert result.sectors == []
    assert result.eligible_stock_weight == 0.0
    assert result.method_b_denominator.startswith("Not applicable")
    assert "no eligible stocks" in result.message
    assert check(result, "Method B totals 100%").status is CheckStatus.NOT_APPLICABLE
    assert check(result, "Stocks have a sector").status is CheckStatus.NOT_APPLICABLE
    assert result.excluded[0].weight == pytest.approx(1.0, abs=1e-12)
    assert result.verification is VerificationStatus.INDEPENDENTLY_RECONCILED


def test_residual_uses_all_holdings_and_is_a_warning(db):
    result = calculate_exposure(db, pid(db, "Total Stock Market Index Fund"))
    assert result.residual == pytest.approx(0.02, abs=1e-12)
    assert result.residual_reported
    residual_check = check(result, "Weights total 100%")
    assert residual_check.status is CheckStatus.WARNING
    assert "98.00%" in residual_check.detail and "2.00%" in residual_check.detail
    assert any("not cash" in c for c in result.caveats)
    assert [pct(s.method_b) for s in result.sectors] == ["48.98", "16.33", "14.29", "10.20", "6.12", "4.08"]


def test_residual_counts_bonds_too():
    # 0.50 stock + 0.30 bond: residual is 100% - 80% = 20%, not 100% - 50%.
    conn = synthetic_db([("AAA", "Stock", "Technology", 0.50), ("BND", "Bond", None, 0.30)])
    result = calculate_exposure(conn, 1)
    assert result.residual == pytest.approx(0.20, abs=1e-12)
    assert result.method_a_total == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("name", [n for n in EXPECTED_METHOD_A if n not in EXPECTED_TOTAL])
def test_portfolios_totalling_100_have_no_residual_line(db, name):
    result = calculate_exposure(db, pid(db, name))
    assert not result.residual_reported
    assert check(result, "Weights total 100%").status is CheckStatus.PASS


# --- source anomalies are preserved and reported ------------------------------------------------

def test_source_anomalies_are_preserved_and_flagged(db):
    result = calculate_exposure(db, pid(db, "International Equity Fund"))
    staples = next(s for s in result.sectors if s.sector == "Consumer Staples")
    assert [h.symbol for h in staples.holdings] == ["HD"]  # supplied classification kept
    assert SECURITY_NOTES["HD"] in result.caveats
    assert ALL_US_NOTE in result.caveats


@pytest.mark.parametrize("name, expect_note", [
    ("Emerging Markets Fund", True),  # strategy International
    ("Total International Index Fund", True),  # name says International
    ("Growth Equity Fund", False),
])
def test_all_us_note_only_where_geography_is_implied(db, name, expect_note):
    assert (ALL_US_NOTE in calculate_exposure(db, pid(db, name)).caveats) is expect_note


def test_portfolio_without_anomalies_has_no_caveats(db):
    assert calculate_exposure(db, pid(db, "Growth Equity Fund")).caveats == []


# --- invalid, missing and unclassified data (synthetic databases) -------------------------------

def test_missing_weight_is_a_warning_and_is_excluded():
    conn = synthetic_db([("AAA", "Stock", "Technology", 0.50), ("BBB", "Stock", "Energy", None),
                         ("BND", "Bond", None, 0.30)])
    result = calculate_exposure(conn, 1)
    assert result.status == "ok"
    assert check(result, "Weights present").status is CheckStatus.WARNING
    assert "BBB" in check(result, "Weights present").detail
    assert [(s.sector, s.method_a, s.method_b) for s in result.sectors] == [("Technology", 0.5, 1.0)]
    assert result.residual == pytest.approx(0.20, abs=1e-12)  # the missing weight stays unattributed
    assert any("BBB" in c for c in result.caveats)
    assert result.verification is VerificationStatus.INDEPENDENTLY_RECONCILED


@pytest.mark.parametrize("bad_weight", [-0.10, 1.50])
def test_invalid_weight_fails_and_produces_no_figures(bad_weight):
    conn = synthetic_db([("AAA", "Stock", "Technology", 0.60), ("BBB", "Stock", "Energy", bad_weight)])
    result = calculate_exposure(conn, 1)
    assert result.status == "invalid_data"
    assert result.sectors == [] and result.excluded == []
    assert check(result, "Weights valid (0 to 1)").status is CheckStatus.FAIL
    assert "BBB" in result.message
    assert result.verification is VerificationStatus.VERIFICATION_FAILED


def test_stock_without_sector_is_excluded_and_warned():
    conn = synthetic_db([("AAA", "Stock", "Technology", 0.70), ("ZZZ", "Stock", None, 0.30)])
    result = calculate_exposure(conn, 1)
    assert check(result, "Stocks have a sector").status is CheckStatus.WARNING
    assert [(e.label, e.weight) for e in result.excluded] == [(UNCLASSIFIED_LABEL, 0.30)]
    assert result.sectors[0].method_b == pytest.approx(1.0)  # denominator is the eligible 0.70 only
    assert result.method_b_denominator == "Eligible stock weight (70.00% of portfolio)"
    assert result.method_a_total == pytest.approx(1.0, abs=1e-12)


def test_portfolio_without_holdings_is_explained_not_divided():
    conn = synthetic_db([])
    result = calculate_exposure(conn, 1)
    assert result.status == "no_eligible_stocks"
    assert "no holdings" in result.message
    assert result.residual == pytest.approx(1.0) and result.residual_reported


def test_floating_point_noise_is_not_reported_as_a_residual():
    weights = [0.1] * 10
    naive = 0.0
    for w in weights:  # plain left-to-right addition drifts to 0.9999999999999999
        naive += w
    conn = synthetic_db([(f"S{i}", "Stock", "Technology", w) for i, w in enumerate(weights)])
    result = calculate_exposure(conn, 1)
    assert naive != 1.0 and math.fsum(weights) == 1.0
    assert result.total_weight == 1.0
    assert not result.residual_reported
    assert result.reconciliation.max_abs_difference <= 1e-12


def test_reconciliation_failure_is_never_shown_as_verified(db, monkeypatch):
    real_sql_path = exposure._sql_path

    def skewed(conn, portfolio_id):
        figures = real_sql_path(conn, portfolio_id)
        return dataclasses.replace(figures, method_a={k: v + 0.01 for k, v in figures.method_a.items()})

    monkeypatch.setattr(exposure, "_sql_path", skewed)
    result = calculate_exposure(db, pid(db, "Balanced Portfolio"))
    assert result.reconciliation.status is CheckStatus.FAIL
    assert result.verification is VerificationStatus.VERIFICATION_FAILED


def test_unknown_portfolio_id_raises(db):
    with pytest.raises(LookupError):
        calculate_exposure(db, 999)


# --- Q9 and Q10 fixtures (hand-derived, evaluation/fixtures) ------------------------------------

@pytest.mark.parametrize("fixture", ["q09_tech_innovation_fund.json", "q10_international_equity_fund.json"])
def test_engine_matches_independent_fixture(db, fixture):
    data = json.loads((PROJECT_ROOT / "evaluation" / "fixtures" / fixture).read_text(encoding="utf-8"))
    expected = data["expected"]
    assert math.fsum(h["current_weight"] for h in data["holdings"]) == pytest.approx(expected["total_weight"])
    result = calculate_exposure(db, pid(db, data["portfolio_name"]))
    assert {s.sector: s.method_a for s in result.sectors} == pytest.approx(expected["method_a"], abs=1e-12)
    assert {s.sector: s.method_b for s in result.sectors} == pytest.approx(expected["method_b"], abs=1e-12)
    assert result.eligible_stock_weight == pytest.approx(expected["eligible_stock_weight"], abs=1e-12)
    assert result.residual == pytest.approx(expected["residual"], abs=1e-12)
