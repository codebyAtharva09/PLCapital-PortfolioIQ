"""Financial Invariant Engine: database-wide checks on the supplied data (no LLM, read-only)."""

import json
import sqlite3

import pytest

from portfolioiq.cli import main
from portfolioiq.config import PROJECT_ROOT, load_settings
from portfolioiq.db import create_reference_db
from portfolioiq.invariants import open_for_checks, run_invariants, weight_totals
from portfolioiq.verification import CheckStatus

PASS, WARNING, FAIL, NA = CheckStatus.PASS, CheckStatus.WARNING, CheckStatus.FAIL, CheckStatus.NOT_APPLICABLE
EXPECTED = {
    "I01": PASS, "I02": PASS, "I03": PASS, "I04": PASS, "I05": PASS, "I06": WARNING, "I07": PASS, "I08": PASS,
    "I09": PASS, "I10": PASS, "I11": WARNING, "I12": WARNING, "I13": WARNING, "I14": PASS, "I15": WARNING,
    "I16": PASS, "I17": PASS, "I18": WARNING, "I19": WARNING, "I20": WARNING, "I21": PASS, "I22": PASS,
}


@pytest.fixture(scope="module")
def report():
    settings = load_settings()
    conn = create_reference_db(settings.data_dir, settings.schema_path)
    try:
        yield run_invariants(conn, settings.data_dir)
    finally:
        conn.close()


def checks_by_id(result):
    return {c.id: c for c in result.checks}


@pytest.fixture
def mutable():
    """A writable in-memory copy of the supplied data, for planting defects."""
    settings = load_settings()
    conn = create_reference_db(settings.data_dir, settings.schema_path)
    yield conn
    conn.close()


def test_supplied_data_statuses(report):
    assert {c.id: c.status for c in report.checks} == EXPECTED
    assert not report.has_failures and report.as_of == "2023-10-09"
    assert (report.count(PASS), report.count(WARNING), report.count(FAIL)) == (14, 8, 0)


def test_documented_anomalies_are_warnings_with_their_figures(report):
    checks = checks_by_id(report)
    assert "Total Stock Market Index Fund 98.00% (residual 2.00%)" in checks["I06"].detail
    assert "1.68% to 14.72%" in checks["I12"].detail and checks["I12"].rule == "D4"
    assert "22 of 23 positions" in checks["I13"].detail
    assert "4 of 13 portfolios" in checks["I15"].detail
    assert "Home Depot" in checks["I18"].detail
    assert "30 of 30 securities" in checks["I11"].detail and checks["I11"].rule == "B11"
    assert checks["I01"].scope == "10 files"  # runtime checks cover the data files only
    assert all(c.effect for c in report.checks)  # every check says what it means for answers


def test_weight_totals_show_the_residual(report):
    totals = {t.portfolio_name: t for t in report.weight_totals}
    assert len(totals) == 13
    assert totals["Total Stock Market Index Fund"].residual == pytest.approx(0.02)
    assert totals["Balanced Portfolio"].bond_weight == pytest.approx(0.64)
    assert totals["Fixed Income Plus"].stock_weight == 0


def test_invalid_and_missing_weights(mutable):
    mutable.execute("UPDATE holdings SET current_weight = NULL WHERE holding_id = 1")
    assert checks_by_id(run_invariants(mutable))["I04"].status is WARNING
    mutable.execute("UPDATE holdings SET current_weight = 1.5 WHERE holding_id = 2")
    check = checks_by_id(run_invariants(mutable))["I04"]
    assert check.status is FAIL and check.rows


def test_foreign_key_violation_fails(mutable):
    mutable.execute("PRAGMA foreign_keys = OFF")
    mutable.execute("INSERT INTO transactions (portfolio_id, security_id, transaction_type, quantity, price, "
                    "transaction_date) VALUES (1, 999, 'BUY', 10, 5.0, '2023-10-01')")
    result = run_invariants(mutable)
    assert checks_by_id(result)["I03"].status is FAIL and result.has_failures


def test_price_and_transaction_defects(mutable):
    mutable.execute("UPDATE securities SET current_price = -1 WHERE symbol = 'AAPL'")
    mutable.execute("UPDATE transactions SET price = 0 WHERE transaction_id = 1")
    mutable.execute("UPDATE holdings SET cost_basis = cost_basis + 1 WHERE holding_id = 3")
    checks = checks_by_id(run_invariants(mutable))
    assert checks["I10"].status is FAIL and checks["I14"].status is FAIL
    assert checks["I09"].status is WARNING  # a stored value differs; it is reported, never recomputed


def test_duplicate_holdings_fail_on_a_schema_without_the_unique_constraint():
    schema = (PROJECT_ROOT / "data" / "database_schema.sql").read_text(encoding="utf-8")
    loose = schema.replace("    FOREIGN KEY (security_id) REFERENCES securities(security_id),\n    UNIQUE(portfolio_id, security_id)",
                           "    FOREIGN KEY (security_id) REFERENCES securities(security_id)")
    assert loose != schema
    conn = sqlite3.connect(":memory:")
    conn.executescript(loose)
    conn.execute("INSERT INTO sectors VALUES (1, 'Technology', NULL, NULL)")
    conn.execute("INSERT INTO securities (security_id, symbol, company_name, asset_type, sector_id, current_price, "
                 "country, currency) VALUES (1, 'AAA', 'A Corp', 'Stock', 1, 10, 'US', 'USD')")
    conn.execute("INSERT INTO portfolios (portfolio_id, portfolio_name, total_aum) VALUES (1, 'Test Fund', 1000)")
    conn.executemany("INSERT INTO holdings (portfolio_id, security_id, quantity, purchase_price, current_weight, "
                     "cost_basis) VALUES (1, 1, 1, 10, 0.5, 10)", [(), ()])
    result = run_invariants(conn)  # a minimal database must not break any check
    checks = checks_by_id(result)
    assert checks["I05"].status is FAIL and checks["I01"].status is NA
    assert checks["I13"].status is NA and checks["I11"].status is NA
    conn.close()


def test_checksum_mismatch_fails(tmp_path):
    (tmp_path / "holdings.csv").write_text("changed", encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text("0" * 64 + "  data/holdings.csv\n" + "1" * 64 + "  evaluation/other.json\n",
                                         encoding="utf-8")
    settings = load_settings()
    conn = create_reference_db(settings.data_dir, settings.schema_path)
    check = checks_by_id(run_invariants(conn, tmp_path))["I01"]
    conn.close()
    assert check.status is FAIL and check.scope == "1 files"


def test_check_connection_is_read_only(readonly_db):
    conn = open_for_checks(readonly_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM holdings")
    assert weight_totals(conn)  # reads still work
    conn.close()


def test_integrity_command(capsys):
    assert main(["integrity"]) == 0
    printed = capsys.readouterr().out
    assert "22 checks on the supplied data (as of 2023-10-09)" in printed and "0 FAIL" in printed
    assert "I06 WARNING" in printed
    assert main(["integrity", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["checks"]) == 22 and data["as_of"] == "2023-10-09"
