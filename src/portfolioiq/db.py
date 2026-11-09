"""Build the reference SQLite database from the supplied schema and CSV files.

The supplied database_schema.sql is executed unmodified, so its constraints and
indexes are kept (loading with pandas.to_sql(if_exists="replace") would drop them).
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

# Parent tables first, so every foreign key already exists when a child row is inserted.
TABLE_LOAD_ORDER = (
    "sectors",
    "securities",
    "benchmarks",
    "portfolios",
    "holdings",
    "transactions",
    "historical_prices",
    "portfolio_performance",
    "risk_metrics",
)


def create_reference_db(data_dir: Path, schema_path: Path) -> sqlite3.Connection:
    """Return a new in-memory database holding the supplied schema and data."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    with conn:  # a single transaction: either every table loads or none does
        for table in TABLE_LOAD_ORDER:
            _load_csv(conn, table, data_dir / f"{table}.csv")
    return conn


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    # Table names come from our own constant, never from user input.
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLE_LOAD_ORDER}


def _load_csv(conn: sqlite3.Connection, table: str, path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = [[_blank_to_none(row[c]) for c in columns] for row in reader]

    known = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    unknown = [c for c in columns if c not in known]
    if unknown:
        raise ValueError(f"{path.name}: columns not in schema table '{table}': {unknown}")

    column_list = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})", rows)


def _blank_to_none(value: str) -> str | None:
    # An empty CSV cell means "no value" (e.g. a bond has no sector); store NULL, not ''.
    # Numeric text such as "102.50" is converted by the column's REAL/INTEGER affinity.
    return None if value.strip() == "" else value
