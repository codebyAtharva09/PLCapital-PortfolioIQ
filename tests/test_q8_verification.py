"""Independent verification of counts and of the assessment's diversification ratio, rebuilt from source records."""

import dataclasses
import json
import shutil
import sqlite3

import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.financial_verification import SourceRecords, diversification_by_portfolio, verify_sql_result
from portfolioiq.sql_engine import execute_sql, open_read_only
from portfolioiq.verification import VerificationStatus as V
from portfolioiq_eval import official

Q8 = next(c for c in official.load_cases() if c.id == "Q8")
JOIN = "FROM portfolios p JOIN holdings h ON p.portfolio_id = h.portfolio_id JOIN securities s ON h.security_id = s.security_id"
SECTOR = "COUNT(DISTINCT s.sector_id)"
TOTAL = "COUNT(h.holding_id)"
GOOD = (f"SELECT p.portfolio_name, {SECTOR} AS sector_count, {TOTAL} AS total_holdings, CAST({SECTOR} AS REAL) / {TOTAL} AS diversification_ratio "
        f"{JOIN} WHERE 1 = 1 GROUP BY p.portfolio_id, p.portfolio_name HAVING {SECTOR} > 5")


@pytest.fixture(scope="module")
def source(readonly_db):
    conn = open_read_only(readonly_db)
    try:
        return SourceRecords.load(conn)
    finally:
        conn.close()


@pytest.fixture()
def verify(readonly_db, schema, source):
    def run(sql, question=Q8.question, records=None):
        result = execute_sql(readonly_db, sql, max_rows=500)
        assert result.status == "ok", result.error
        return verify_sql_result(sql, result.columns, result.rows, schema, records or source, question=question)
    return run


def test_the_definition_is_rebuilt_from_the_records_for_the_documented_portfolio(source):
    assert diversification_by_portfolio(source)["Total Stock Market Index Fund"] == (6, 12, 0.5)
    selected = [name for name, (count, _, _) in diversification_by_portfolio(source).items() if count > 5]
    assert selected == ["Total Stock Market Index Fund"]


def test_the_reference_query_reconciles(verify):
    result = verify(Q8.reference_sql)
    assert result.status is V.INDEPENDENTLY_RECONCILED, result.summary
    assert len(result.figures) == 3 and result.rows_checked == 1


def test_the_recorded_production_answer_to_q8_reconciles(verify):
    runs = sorted((PROJECT_ROOT / "reports" / "sprint_v2_3" / "candidate" / "runs").glob("*/sql_outputs.jsonl"))
    records = [json.loads(line) for path in runs for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sql = next(r["result"]["sql"] for r in records if r["case"]["id"] == "Q8")
    result = verify(sql)
    assert result.status is V.INDEPENDENTLY_RECONCILED, result.summary


def test_a_differently_written_correct_query_reconciles(verify):
    result = verify(GOOD)
    assert result.status is V.INDEPENDENTLY_RECONCILED, result.summary


RATIO = f"CAST({SECTOR} AS REAL) / {TOTAL} AS"
HAVING = f"HAVING {SECTOR} > 5"
WRONG = {
    "ratio inverted": GOOD.replace(RATIO, f"CAST({TOTAL} AS REAL) / {SECTOR} AS"),
    "numerator without DISTINCT": GOOD.replace(RATIO, f"CAST(COUNT(s.sector_id) AS REAL) / {TOTAL} AS"),
    "sector column without DISTINCT": GOOD.replace(f"{SECTOR} AS sector_count", "COUNT(s.sector_id) AS sector_count"),
    "threshold too low": GOOD.replace(HAVING, f"HAVING {SECTOR} > 3"),
    "threshold too high": GOOD.replace(HAVING, f"HAVING {SECTOR} >= 7"),
    "wrong portfolio": GOOD.replace("WHERE 1 = 1", "WHERE p.portfolio_name = 'Balanced Portfolio'").replace(HAVING, ""),
    "wrong GROUP BY": GOOD.replace("GROUP BY p.portfolio_id, p.portfolio_name", "GROUP BY s.asset_type").replace(HAVING, ""),
    "no grouping": GOOD.replace("GROUP BY p.portfolio_id, p.portfolio_name", ""),
    "fan-out join": GOOD.replace(JOIN, JOIN + " JOIN historical_prices x ON x.security_id = s.security_id"),
    "one stock omitted": GOOD.replace("WHERE 1 = 1", "WHERE s.symbol <> 'AAPL'"),
}


@pytest.mark.parametrize("name", WRONG)
def test_wrong_calculations_fail(verify, name):
    result = verify(WRONG[name])
    assert result.status is V.VERIFICATION_FAILED, result.summary


# These only differ from a correct answer on data the supplied database does not contain: its one qualifying fund holds no bond, and every
# bond has no sector. So they run against a copy in which that fund also holds a bond.

def with_bond(readonly_db, tmp_path, sector_id):
    copy = tmp_path / "with_bond.db"
    shutil.copy(readonly_db, copy)
    conn = sqlite3.connect(copy)
    bond = conn.execute("SELECT security_id FROM securities WHERE asset_type = 'Bond' LIMIT 1").fetchone()[0]
    conn.execute("UPDATE securities SET sector_id = ? WHERE security_id = ?", (sector_id, bond))
    conn.execute("INSERT INTO holdings (holding_id, portfolio_id, security_id, quantity) VALUES (9001, 11, ?, 10)", (bond,))
    conn.commit()
    conn.close()
    return copy


def outcome_on(copy, schema, sql):
    result = execute_sql(copy, sql)
    assert result.status == "ok", result.error
    conn = open_read_only(copy)
    try:
        records = SourceRecords.load(conn)
    finally:
        conn.close()
    assert diversification_by_portfolio(records)["Total Stock Market Index Fund"] == (6, 13, 6 / 13)
    return verify_sql_result(sql, result.columns, result.rows, schema, records, question=Q8.question)


def unused_sector(readonly_db):
    """A sector none of the fund's stocks is in, so counting a bond under it would add a seventh."""
    conn = sqlite3.connect(readonly_db)
    try:
        return conn.execute("SELECT MIN(sector_id) FROM sectors WHERE sector_id NOT IN (SELECT s.sector_id FROM holdings h JOIN securities s ON s.security_id = h.security_id "
                            "WHERE h.portfolio_id = 11 AND s.sector_id IS NOT NULL)").fetchone()[0]
    finally:
        conn.close()


def test_the_definition_counts_a_bond_in_holdings_but_not_in_sectors(readonly_db, schema, tmp_path):
    result = outcome_on(with_bond(readonly_db, tmp_path, unused_sector(readonly_db)), schema, Q8.reference_sql)
    assert result.status is V.INDEPENDENTLY_RECONCILED, result.summary


@pytest.mark.parametrize("sector, wrong", [
    ("unused", GOOD),  # counts the bond's sector as a stock sector
    (None, GOOD.replace("WHERE 1 = 1", "WHERE s.asset_type = 'Stock'")),  # total holdings restricted to stocks
    (None, GOOD.replace(RATIO, f"CAST({SECTOR} AS REAL) / COUNT(s.sector_id) AS")),  # a null-sector bond drops out of the denominator
    (None, GOOD.replace(f"{TOTAL} AS total_holdings", "COUNT(s.sector_id) AS total_holdings")),
], ids=["bond sector counted", "holdings restricted to stocks", "denominator drops null sector", "holdings column drops null sector"])
def test_errors_that_need_a_bond_in_the_fund_fail(readonly_db, schema, tmp_path, sector, wrong):
    copy = with_bond(readonly_db, tmp_path, unused_sector(readonly_db) if sector else None)
    result = outcome_on(copy, schema, wrong)
    assert result.status is V.VERIFICATION_FAILED, result.summary


def test_rows_out_of_their_stated_order_fail(readonly_db, schema, source):
    sql = GOOD.replace(HAVING, f"HAVING {SECTOR} > 3") + " ORDER BY diversification_ratio DESC"
    question = "Find portfolios that have holdings in more than 3 different sectors and show their diversification metrics"
    result = execute_sql(readonly_db, sql)
    assert len(result.rows) > 1
    assert verify_sql_result(sql, result.columns, result.rows, schema, source, question=question).status is V.INDEPENDENTLY_RECONCILED
    assert verify_sql_result(sql, result.columns, list(reversed(result.rows)), schema, source, question=question).status is V.VERIFICATION_FAILED


@pytest.mark.parametrize("question", ["Which three portfolios are the most diversified?", "Which portfolio is best diversified?"], ids=range(2))
def test_selections_the_check_does_not_model_are_not_verifiable_rather_than_failed(verify, question):
    assert verify(GOOD, question).status is V.NOT_INDEPENDENTLY_VERIFIABLE


def test_a_diversification_question_about_one_named_portfolio_reconciles(verify):
    sql = GOOD.replace("WHERE 1 = 1", "WHERE p.portfolio_name = 'Balanced Portfolio'").replace(HAVING, "").replace(SECTOR, "COUNT(DISTINCT CASE WHEN s.asset_type = 'Stock' THEN s.sector_id END)")
    assert verify(sql, "What is the diversification ratio of the Balanced Portfolio?").status is V.INDEPENDENTLY_RECONCILED


def test_wrong_values_fail_rather_than_merely_being_unverified(verify):
    assert verify(GOOD.replace("HAVING " + SECTOR + " > 5", "HAVING " + SECTOR + " > 3")).status is V.VERIFICATION_FAILED
    inverted = GOOD.replace(f"CAST({SECTOR} AS REAL) / {TOTAL} AS", f"CAST({TOTAL} AS REAL) / {SECTOR} AS")
    assert verify(inverted).status is V.VERIFICATION_FAILED


@pytest.mark.parametrize("index, value", [(1, 5), (2, 13), (3, 0.6)], ids=["numerator", "denominator", "ratio"])
def test_a_tampered_figure_fails(readonly_db, schema, source, index, value):
    result = execute_sql(readonly_db, Q8.reference_sql)
    rows = [list(r) for r in result.rows]
    rows[0][index] = value
    assert verify_sql_result(Q8.reference_sql, result.columns, rows, schema, source, question=Q8.question).status is V.VERIFICATION_FAILED


def test_a_missing_or_extra_portfolio_fails(readonly_db, schema, source):
    result = execute_sql(readonly_db, Q8.reference_sql)
    extra = [*map(list, result.rows), ["Balanced Portfolio", 3, 8, 0.375]]
    assert verify_sql_result(Q8.reference_sql, result.columns, extra, schema, source, question=Q8.question).status is V.VERIFICATION_FAILED


def test_the_ratio_must_be_present(verify):
    no_ratio = f"SELECT p.portfolio_name, {SECTOR} AS sector_count, {TOTAL} AS total_holdings {JOIN} GROUP BY p.portfolio_id, p.portfolio_name HAVING {SECTOR} > 5"
    assert verify(no_ratio).status is V.VERIFICATION_FAILED


# --- corrupted source records ------------------------------------------------------------------------------------------

def corrupted(source, change):
    return dataclasses.replace(source, **change(source))


def test_duplicate_holdings_in_the_source_are_detected_on_load(readonly_db, schema):
    copy = sqlite3.connect(":memory:")
    disk = sqlite3.connect(f"{readonly_db.resolve().as_uri()}?mode=ro", uri=True)
    disk.backup(copy)
    disk.close()
    for table, key, repeated in (("holdings", "holding_id", "portfolio_id = (SELECT MIN(portfolio_id) FROM holdings)"), ("sectors", "sector_id", "sector_id = 1")):
        copy.execute(f"CREATE TABLE {table}_loose AS SELECT * FROM {table}")  # no keys or uniqueness, so a duplicate can exist
        copy.execute(f"DROP TABLE {table}")
        copy.execute(f"ALTER TABLE {table}_loose RENAME TO {table}")
        copy.execute(f"INSERT INTO {table} SELECT * FROM {table} WHERE {repeated}")
    records = SourceRecords.load(copy)
    copy.close()
    assert any("more than once" in p for p in records.problems) and any("sector id" in p for p in records.problems)
    result = verify_sql_result(GOOD, ["portfolio_name"], [["x"]], schema, records, question=Q8.question)
    assert result.status is not V.INDEPENDENTLY_RECONCILED


def test_records_flagged_as_unreliable_are_never_reconciled(readonly_db, schema, source):
    result = execute_sql(readonly_db, Q8.reference_sql)
    broken = corrupted(source, lambda s: {"problems": ("a sector id appears more than once",)})
    outcome = verify_sql_result(Q8.reference_sql, result.columns, result.rows, schema, broken, question=Q8.question)
    assert outcome.status is V.NOT_INDEPENDENTLY_VERIFIABLE and "integrity" in outcome.summary


def in_sector_of(symbol):
    return lambda s: next(h["securities.sector_id"] for h in s.holdings if h["securities.symbol"] == symbol)


@pytest.mark.parametrize("change, why", [
    (lambda s: {"holdings": tuple(h for h in s.holdings if h["securities.symbol"] != "AAPL")}, "a holding is missing"),
    (lambda s: {"holdings": tuple({**h, "securities.asset_type": "Bond"} if h["securities.sector_id"] == in_sector_of("JPM")(s) else h for h in s.holdings)}, "stocks became bonds"),
    (lambda s: {"holdings": tuple({**h, "securities.sector_id": None} if h["securities.sector_id"] == in_sector_of("JPM")(s) else h for h in s.holdings)}, "stocks lost their sector"),
], ids=["missing", "stock-as-bond", "null-sector"])
def test_the_verifier_follows_the_records_not_the_query(readonly_db, schema, source, change, why):
    # the query's answer comes from the intact data; records that differ must expose the disagreement
    result = execute_sql(readonly_db, Q8.reference_sql)
    records = corrupted(source, change)
    assert diversification_by_portfolio(records).get("Total Stock Market Index Fund") != (6, 12, 0.5)
    assert verify_sql_result(Q8.reference_sql, result.columns, result.rows, schema, records, question=Q8.question).status is V.VERIFICATION_FAILED, why


# --- counts in general ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sql, question", [
    ("SELECT COUNT(*) FROM portfolios", "How many portfolios do we have?"),
    ("SELECT COUNT(*) FROM portfolios WHERE status = 'Active'", "How many active portfolios are there?"),
    ("SELECT COUNT(*) FROM securities WHERE asset_type = 'Bond'", "How many bonds are there?"),
    ("SELECT COUNT(*) FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id WHERE p.portfolio_name = 'Dividend Aristocrats Fund'", "How many holdings does the Dividend Aristocrats Fund have?"),
    ("SELECT p.portfolio_name, COUNT(*) AS n FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id GROUP BY p.portfolio_name", "How many holdings does each portfolio have?"),
    ("SELECT s.asset_type, COUNT(*) AS n FROM securities s GROUP BY s.asset_type", "How many securities are there of each asset type?"),
    ("SELECT sec.sector_name, COUNT(*) AS n FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id GROUP BY sec.sector_name", "How many securities in each sector?"),
], ids=range(7))
def test_counts_reconcile(verify, sql, question):
    result = verify(sql, question)
    assert result.status is V.INDEPENDENTLY_RECONCILED, result.summary


def test_a_filter_on_a_different_portfolio_from_the_question_fails(verify):
    sql = "SELECT COUNT(*) FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id WHERE p.portfolio_name = 'Balanced Portfolio'"
    assert verify(sql, "How many holdings does the Dividend Aristocrats Fund have?").status is V.VERIFICATION_FAILED


def test_counts_over_unsupported_tables_stay_unverifiable(verify):
    assert verify("SELECT COUNT(*) FROM transactions", "How many transactions are there?").status is V.NOT_INDEPENDENTLY_VERIFIABLE


def test_a_tampered_count_fails(readonly_db, schema, source):
    sql = "SELECT COUNT(*) FROM portfolios"
    assert verify_sql_result(sql, ["n"], [[14]], schema, source).status is V.VERIFICATION_FAILED
