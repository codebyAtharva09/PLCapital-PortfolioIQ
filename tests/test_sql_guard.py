"""Milestone 3: the SQLGlot guard accepts one read-only SELECT and rejects everything else."""

import pytest

from portfolioiq.sql_guard import SECURITY_CATEGORIES, validate_sql


def test_every_reference_query_passes_the_guard(schema, reference_sql):
    for question_id, sql in reference_sql.items():
        result = validate_sql(sql, schema)
        assert result.ok, f"Q{question_id}: {result.reason}"
        assert result.statement_type == "SELECT"


@pytest.mark.parametrize("sql, category", [
    ("", "empty"),
    ("   ", "empty"),
    ("x" * 10_001, "too_long"),
    ("SELECT 1; SELECT 2", "multiple_statements"),
    ("SELECT * FROM portfolios; DROP TABLE holdings", "multiple_statements"),
    ("DELETE FROM holdings", "forbidden_operation"),
    ("INSERT INTO sectors VALUES (99, 'x', 'y', 'z')", "forbidden_operation"),
    ("REPLACE INTO sectors VALUES (99, 'x', 'y', 'z')", "forbidden_operation"),
    ("UPDATE portfolios SET total_aum = 0", "forbidden_operation"),
    ("DROP TABLE holdings", "forbidden_operation"),
    ("CREATE TABLE t (a)", "forbidden_operation"),
    ("ALTER TABLE holdings ADD COLUMN z", "forbidden_operation"),
    ("PRAGMA table_info(holdings)", "forbidden_operation"),
    ("ATTACH DATABASE 'x.db' AS x", "forbidden_operation"),
    ("DETACH DATABASE x", "forbidden_operation"),
    ("VACUUM", "forbidden_operation"),
    ("BEGIN", "forbidden_operation"),
    ("WITH d AS (DELETE FROM holdings RETURNING *) SELECT * FROM d", "forbidden_operation"),
    ("SELECT * INTO copy_table FROM portfolios", "forbidden_operation"),
    ("SELECT * FROM sqlite_master", "forbidden_table"),
    ("SELECT * FROM main.portfolios", "qualified_name"),
    ("SELECT load_extension('evil')", "forbidden_function"),
    ("SELEC * FRM portfolios", "not_select"),  # SQLGlot parses this leniently as an alias
    ("SELECT nonexistent FROM portfolios", "unknown_column"),
    ("SELECT * FROM unknown_table", "unknown_table"),
    ("SELECT x.portfolio_name FROM portfolios AS p", "unknown_table"),
    ('SELECT portfolio_name FROM portfolios WHERE status = "Active"', "unknown_column"),  # "..." is an identifier
])
def test_rejections(schema, sql, category):
    result = validate_sql(sql, schema)
    assert (result.ok, result.category) == (False, category), result.reason
    assert result.reason


def test_table_valued_pragma_function_is_rejected(schema):
    result = validate_sql("SELECT * FROM pragma_table_info('holdings')", schema)
    assert not result.ok and result.category in SECURITY_CATEGORIES


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM portfolios",
    "WITH c(n) AS (SELECT COUNT(*) FROM portfolios) SELECT n FROM c",
    "SELECT symbol, ROW_NUMBER() OVER (ORDER BY current_price DESC) AS rn FROM securities ORDER BY rn",
    "SELECT h.* FROM holdings AS h",
    "SELECT portfolio_name FROM portfolios UNION SELECT benchmark_name FROM benchmarks",
    "SELECT t.cnt FROM (SELECT COUNT(*) AS cnt FROM holdings) AS t",
    "SELECT strftime('%Y', transaction_date) AS yr, COUNT(*) FROM transactions GROUP BY yr",
    "SELECT 1 -- ; DROP TABLE holdings",
    "SELECT symbol FROM securities WHERE company_name LIKE '%Inc%'",
])
def test_legitimate_queries_are_accepted(schema, sql):
    result = validate_sql(sql, schema)
    assert result.ok, result.reason


def test_validation_records_the_tables_read(schema):
    result = validate_sql("SELECT p.portfolio_name FROM portfolios p JOIN holdings h "
                          "ON h.portfolio_id = p.portfolio_id", schema)
    assert result.tables == ["holdings", "portfolios"]


def test_security_categories_are_never_repairable():
    assert {"forbidden_operation", "multiple_statements", "forbidden_table"} <= SECURITY_CATEGORIES
    assert "unknown_column" not in SECURITY_CATEGORIES and "parse_error" not in SECURITY_CATEGORIES
