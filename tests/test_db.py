"""Milestone 1: the reference database matches the supplied schema and data exactly."""

import hashlib
import re

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.db import TABLE_LOAD_ORDER, row_counts

EXPECTED_ROW_COUNTS = {
    "sectors": 10,
    "securities": 30,
    "benchmarks": 13,
    "portfolios": 13,
    "holdings": 95,
    "transactions": 24,
    "historical_prices": 330,
    "portfolio_performance": 44,
    "risk_metrics": 44,
}

NUMERIC_COLUMNS = [
    ("securities", "current_price"),
    ("securities", "market_cap"),
    ("portfolios", "total_aum"),
    ("holdings", "quantity"),
    ("holdings", "current_weight"),
    ("holdings", "cost_basis"),
    ("transactions", "price"),
    ("historical_prices", "close_price"),
    ("portfolio_performance", "nav"),
    ("risk_metrics", "var_95"),
]


def test_all_nine_tables_exist(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert names == set(TABLE_LOAD_ORDER)


def test_every_index_in_the_schema_file_exists(db):
    schema = (PROJECT_ROOT / "data" / "database_schema.sql").read_text(encoding="utf-8")
    declared = set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", schema))
    present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert len(declared) == 18
    assert declared <= present


def test_row_counts_match_the_csv_files(db):
    assert row_counts(db) == EXPECTED_ROW_COUNTS


def test_blank_csv_cells_are_stored_as_null(db):
    bonds = db.execute(
        "SELECT sector_id, market_cap FROM securities WHERE asset_type = 'Bond'"
    ).fetchall()
    assert len(bonds) == 5
    assert all(sector is None and cap is None for sector, cap in bonds)
    blanks = db.execute(
        "SELECT COUNT(*) FROM securities WHERE sector_id = '' OR market_cap = ''"
    ).fetchone()[0]
    assert blanks == 0


def test_numeric_columns_are_stored_as_numbers(db):
    for table, column in NUMERIC_COLUMNS:
        types = {
            r[0]
            for r in db.execute(
                f"SELECT DISTINCT typeof({column}) FROM {table} WHERE {column} IS NOT NULL"
            )
        }
        assert types <= {"real", "integer"}, f"{table}.{column} stored as {types}"


def test_foreign_keys_and_integrity(db):
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_supplied_files_are_byte_identical_to_the_originals():
    lines = (PROJECT_ROOT / "data" / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 11
    for line in lines:
        expected, rel_path = line.split(maxsplit=1)
        actual = hashlib.sha256((PROJECT_ROOT / rel_path).read_bytes()).hexdigest()
        assert actual == expected, f"{rel_path} differs from the supplied original"
