"""Milestone 3: SQLite's own controls block unsafe SQL even when the guard is bypassed."""

import hashlib
import shutil

import pytest

from portfolioiq.config import load_settings
from portfolioiq.sql_engine import (NOT_ALLOWED_MESSAGE, data_fingerprint, ensure_reference_db, execute_sql,
                                    open_read_only)

ATTACKS = [
    "DELETE FROM holdings",
    "INSERT INTO sectors VALUES (99, 'x', 'y', 'z')",
    "UPDATE portfolios SET total_aum = 0",
    "DROP TABLE holdings",
    "CREATE TABLE t (a)",
    "ALTER TABLE holdings ADD COLUMN z",
    "PRAGMA table_info(holdings)",
    "PRAGMA query_only = OFF",
    "ATTACH DATABASE ':memory:' AS x",
    "VACUUM",
    "SELECT * FROM sqlite_master",
    "SELECT load_extension('x')",
    "SELECT randomblob(10)",
    "SELECT random()",
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c",
    "SELECT 1; DELETE FROM holdings",
]


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reference_queries_give_identical_results_read_only(db, readonly_db, reference_sql):
    for question_id, sql in reference_sql.items():
        result = execute_sql(readonly_db, sql)
        assert result.status == "ok", f"Q{question_id}: {result.error}"
        assert result.rows == [list(row) for row in db.execute(sql).fetchall()]


@pytest.mark.parametrize("sql", ATTACKS)
def test_database_controls_block_unsafe_sql_without_the_guard(readonly_db, sql):
    result = execute_sql(readonly_db, sql)
    assert result.status == "error" and result.rows == []
    assert result.error


def test_database_is_unchanged_after_every_attack(readonly_db):
    before = file_hash(readonly_db)
    results = [execute_sql(readonly_db, sql) for sql in ATTACKS]
    assert all(r.status == "error" for r in results)
    assert file_hash(readonly_db) == before
    counts = execute_sql(readonly_db, "SELECT (SELECT COUNT(*) FROM holdings), (SELECT COUNT(*) FROM portfolios), "
                                      "(SELECT COUNT(*) FROM transactions), (SELECT COUNT(*) FROM sectors)")
    assert counts.rows == [[95, 13, 24, 10]]


def test_authorizer_denials_return_a_safe_message(readonly_db):
    assert execute_sql(readonly_db, "DELETE FROM holdings").error == NOT_ALLOWED_MESSAGE
    assert execute_sql(readonly_db, "SELECT * FROM sqlite_master").error == NOT_ALLOWED_MESSAGE


def test_long_running_query_is_stopped(readonly_db):
    sql = ("SELECT SUM(a.close_price * b.close_price * c.close_price) "
           "FROM historical_prices a, historical_prices b, historical_prices c")
    result = execute_sql(readonly_db, sql, timeout_s=0.05)
    assert result.status == "timeout"
    assert "time limit" in result.error


def test_results_are_capped_and_flagged(readonly_db):
    result = execute_sql(readonly_db, "SELECT * FROM historical_prices", max_rows=100)
    assert (result.status, result.row_count, result.truncated) == ("ok", 100, True)
    complete = execute_sql(readonly_db, "SELECT * FROM portfolios", max_rows=100)
    assert (complete.row_count, complete.truncated) == (13, False)


def test_non_finite_numbers_are_counted_not_hidden(readonly_db):
    result = execute_sql(readonly_db, "SELECT 1e308 * 10 AS overflow")
    assert result.non_finite_values == 1 and result.rows == [[None]]


def test_connection_cannot_write_even_when_asked_directly(readonly_db):
    conn = open_read_only(readonly_db)
    try:
        with pytest.raises(Exception):
            conn.execute("DELETE FROM holdings")
    finally:
        conn.close()


def test_reference_db_is_built_once_per_fingerprint(tmp_path):
    settings = load_settings()
    first = ensure_reference_db(settings.data_dir, settings.schema_path, tmp_path)
    modified = first.stat().st_mtime_ns
    assert ensure_reference_db(settings.data_dir, settings.schema_path, tmp_path) == first
    assert first.stat().st_mtime_ns == modified
    assert data_fingerprint(settings.data_dir, settings.schema_path)[:16] in first.name


def test_fingerprint_changes_when_the_data_changes(tmp_path):
    settings = load_settings()
    copy = tmp_path / "data"
    shutil.copytree(settings.data_dir, copy)
    original = data_fingerprint(copy, copy / "database_schema.sql")
    (copy / "sectors.csv").write_text((copy / "sectors.csv").read_text() + "\n", encoding="utf-8")
    assert data_fingerprint(copy, copy / "database_schema.sql") != original
