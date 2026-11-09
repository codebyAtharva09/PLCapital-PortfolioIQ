"""SQLite resource limits, bounded output and table-scoped column validation."""

import json

import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.sql_engine import MAX_RESULT_BYTES, execute_sql, open_read_only
from portfolioiq.sql_guard import validate_sql
from portfolioiq_eval import independent, official, sql_eval


# --- resource limits ------------------------------------------------------------------------------------------------

def test_a_huge_generated_string_is_never_built(readonly_db):
    result = execute_sql(readonly_db, "SELECT printf('%.*c', 40000000, 'x')")  # previously returned a 40 MB value
    assert result.status == "ok" and result.rows == [[None]]


def test_a_string_just_under_the_limit_is_still_allowed(readonly_db):
    result = execute_sql(readonly_db, "SELECT printf('%.*c', 900000, 'x')")
    assert result.status == "ok" and len(result.rows[0][0]) == 900000


def test_total_output_is_bounded_even_under_the_row_limit(readonly_db):
    sql = "SELECT printf('%.*c', 500000, 'x') FROM portfolios"
    result = execute_sql(readonly_db, sql)
    assert result.status == "ok" and result.truncated and result.row_count < 13
    assert sum(len(r[0]) for r in result.rows) <= MAX_RESULT_BYTES


def test_the_row_limit_still_truncates_and_a_normal_result_does_not(readonly_db):
    capped = execute_sql(readonly_db, "SELECT holding_id FROM holdings", max_rows=10)
    assert capped.row_count == 10 and capped.truncated
    full = execute_sql(readonly_db, "SELECT holding_id FROM holdings")
    assert full.row_count == 95 and not full.truncated


def test_attach_is_impossible_at_the_connection_level(readonly_db):
    conn = open_read_only(readonly_db)
    try:
        import sqlite3

        assert conn.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) == 0
    finally:
        conn.close()


def test_the_work_budget_stops_an_expensive_query(readonly_db, monkeypatch):
    monkeypatch.setattr("portfolioiq.sql_engine.MAX_VM_STEPS", 5_000)
    sql = "SELECT COUNT(*) FROM historical_prices a, historical_prices b, historical_prices c"
    assert execute_sql(readonly_db, sql, timeout_s=30.0).status == "timeout"


def test_the_time_limit_is_preserved(readonly_db):
    sql = "SELECT COUNT(*) FROM historical_prices a, historical_prices b, historical_prices c, historical_prices d"
    assert execute_sql(readonly_db, sql, timeout_s=0.05).status == "timeout"


def test_writes_stay_impossible_with_the_guard_bypassed(readonly_db):
    for sql in ("DELETE FROM holdings", "UPDATE portfolios SET total_aum = 0", "DROP TABLE holdings"):
        assert execute_sql(readonly_db, sql).status == "error"


# --- scoped columns ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sql, column", [
    ("SELECT current_price FROM holdings", "current_price"),
    ("SELECT h.current_price FROM holdings h", "current_price"),
    ("SELECT p.symbol FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id", "symbol"),
    ("SELECT h.quantity FROM holdings h WHERE h.sector_name = 'Energy'", "sector_name"),
    ("WITH c AS (SELECT holding_id FROM holdings) SELECT quantity FROM c", "quantity"),
    ("SELECT x.quantity FROM (SELECT holding_id FROM holdings) AS x", "quantity"),
    ("SELECT portfolio_name FROM portfolios ORDER BY current_price", "current_price"),
])
def test_a_column_from_the_wrong_table_is_rejected_statically(schema, sql, column):
    result = validate_sql(sql, schema)
    assert (result.ok, result.category) == (False, "unknown_column") and column in result.reason


@pytest.mark.parametrize("sql", [
    "SELECT h.quantity AS q FROM holdings h ORDER BY q",
    "SELECT portfolio_name AS n FROM portfolios WHERE status = 'Active' ORDER BY n DESC",
    "SELECT s.symbol, COUNT(*) AS n FROM holdings h JOIN securities s ON s.security_id = h.security_id GROUP BY s.symbol HAVING n > 1",
    "WITH c AS (SELECT portfolio_id, SUM(quantity) AS q FROM holdings GROUP BY portfolio_id) SELECT p.portfolio_name, c.q FROM c JOIN portfolios p ON p.portfolio_id = c.portfolio_id",
    "SELECT x.q FROM (SELECT SUM(quantity) AS q FROM holdings) AS x",
    "SELECT p.portfolio_name FROM portfolios p WHERE EXISTS (SELECT 1 FROM holdings h WHERE h.portfolio_id = p.portfolio_id)",
    "SELECT r.beta FROM risk_metrics r WHERE r.calculation_date = (SELECT MAX(x.calculation_date) FROM risk_metrics x WHERE x.portfolio_id = r.portfolio_id)",
    "SELECT portfolio_name FROM portfolios UNION SELECT symbol FROM securities",
    "SELECT beta FROM (SELECT beta, ROW_NUMBER() OVER (PARTITION BY portfolio_id ORDER BY calculation_date DESC) AS n FROM risk_metrics) AS t WHERE n = 1",
])
def test_valid_scoping_is_accepted(schema, sql):
    assert validate_sql(sql, schema).ok, validate_sql(sql, schema).reason


def test_no_reference_query_or_recorded_gemini_draft_is_newly_rejected(schema):
    queries = [c.reference_sql for c in official.load_cases() if c.reference_sql]
    queries += [c.reference_sql for c in sql_eval.load_cases() if getattr(c, "reference_sql", None)]
    queries += [c["reference_sql"] for c in independent.load_cases(("dev", "validation")) if c.get("reference_sql")]
    for path in (PROJECT_ROOT / "reports").rglob("sql_outputs.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result = json.loads(line).get("result") or {}
                queries += [a["sql"] for a in result.get("attempts", []) if a.get("validation", {}) and a["validation"].get("ok")]
    assert len(queries) > 100
    rejected = [q for q in queries if not validate_sql(q, schema).ok]
    assert not rejected, rejected[:3]


def test_the_guard_is_not_weakened(schema):
    for sql, category in (("SELECT 1; DROP TABLE holdings", "multiple_statements"), ("DELETE FROM holdings", "forbidden_operation"),
                          ("SELECT * FROM sqlite_master", "forbidden_table"), ("SELECT nope FROM holdings", "unknown_column"),
                          ("SELECT load_extension('x') FROM holdings", "forbidden_function")):
        assert validate_sql(sql, schema).category == category
