"""Read-only SQL execution: database-level controls that do not depend on the SQL guard.

Every connection used to run a query is restricted by SQLite itself:

- the database file is opened through a read-only URI (``mode=ro``);
- ``PRAGMA query_only = ON``;
- an authorizer allows only SELECT, reads of the nine supplied tables and allow-listed functions.
  It denies writes, schema changes, ATTACH, PRAGMA, recursive CTEs and everything else;
- a progress handler aborts any statement that runs past the time limit or the work budget;
- SQLite resource limits bound string size, SQL length, expression depth and program size;
- Python's sqlite3 refuses to execute more than one statement per call;
- results are capped at a maximum number of rows and a maximum total size.
"""

from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from portfolioiq.db import TABLE_LOAD_ORDER, create_reference_db

ALLOWED_TABLES = frozenset(TABLE_LOAD_ORDER)
# SQLite built-ins judged safe and useful for analytics. Deliberately absent: load_extension,
# randomblob and zeroblob (memory exhaustion), random (non-reproducible), sqlite_* introspection.
ALLOWED_FUNCTIONS = frozenset({
    # aggregates
    "avg", "count", "group_concat", "string_agg", "max", "min", "sum", "total",
    # scalar
    "abs", "coalesce", "concat", "concat_ws", "ifnull", "iif", "instr", "length", "lower", "upper",
    "ltrim", "rtrim", "trim", "nullif", "printf", "format", "replace", "round", "sign", "substr",
    "substring", "typeof", "like", "glob", "char", "unicode", "likely", "unlikely",
    # date and time
    "date", "time", "datetime", "julianday", "strftime", "unixepoch",
    # window
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value",
    # maths
    "ceil", "ceiling", "floor", "mod", "pow", "power", "sqrt", "exp", "ln", "log", "log10", "log2", "trunc",
})

_SQLITE_FUNCTION = getattr(sqlite3, "SQLITE_FUNCTION", 31)
MAX_RESULT_BYTES = 2_000_000  # the whole result; a row cap alone would still allow 500 very long rows
MAX_VM_STEPS = 50_000_000  # SQLite virtual-machine instructions per statement; far beyond any query on this database
_PROGRESS_INTERVAL = 1000
SQLITE_LIMITS = {
    sqlite3.SQLITE_LIMIT_LENGTH: 1_000_000,  # the longest string or row; stops printf('%.*c', ...) building huge values
    sqlite3.SQLITE_LIMIT_SQL_LENGTH: 100_000,
    sqlite3.SQLITE_LIMIT_COLUMN: 200,
    sqlite3.SQLITE_LIMIT_EXPR_DEPTH: 100,
    sqlite3.SQLITE_LIMIT_COMPOUND_SELECT: 20,
    sqlite3.SQLITE_LIMIT_VDBE_OP: 200_000,
    sqlite3.SQLITE_LIMIT_FUNCTION_ARG: 32,
    sqlite3.SQLITE_LIMIT_ATTACHED: 0,
    sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH: 1_000,
    sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER: 32,
    sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH: 1,
}
NOT_ALLOWED_MESSAGE = ("Not allowed: PortfolioIQ runs read-only SELECT queries on the nine portfolio tables "
                       "with approved functions only.")


class SqlExecution(BaseModel):
    """What happened when a validated query ran; recorded in the Financial Answer Receipt."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "error", "timeout"]
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    non_finite_values: int = 0  # infinities/NaN, shown as null in rows; a result check fails on them
    duration_ms: float = 0.0
    error: str | None = None


def data_fingerprint(data_dir: Path, schema_path: Path) -> str:
    """SHA-256 over the supplied CSV files and schema: identifies the data a result came from."""
    digest = hashlib.sha256()
    for path in [*(data_dir / f"{t}.csv" for t in TABLE_LOAD_ORDER), schema_path]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_reference_db(data_dir: Path, schema_path: Path, cache_dir: Path) -> Path:
    """The database file queries run against, built once per data fingerprint and never edited."""
    path = cache_dir / f"portfolioiq-{data_fingerprint(data_dir, schema_path)[:16]}.db"
    if path.exists():
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    source = create_reference_db(data_dir, schema_path)
    handle, temp_name = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    os.close(handle)
    try:
        target = sqlite3.connect(temp_name)
        source.backup(target)
        target.close()
        try:
            os.replace(temp_name, path)
        except PermissionError:  # another process published the identical file first
            if not path.exists():
                raise
    finally:
        source.close()
        if os.path.exists(temp_name):
            os.remove(temp_name)
    return path


def read_schema(db_path: Path) -> dict[str, dict[str, str]]:
    """{table: {column: declared type}} for the supplied tables (trusted internal query)."""
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {
            table: {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in TABLE_LOAD_ORDER
        }
    finally:
        conn.close()


def open_read_only(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")  # set before the authorizer, which denies PRAGMA
    for category, limit in SQLITE_LIMITS.items():
        conn.setlimit(category, limit)
    conn.set_authorizer(_authorizer)
    return conn


def execute_sql(db_path: Path, sql: str, *, timeout_s: float = 2.0, max_rows: int = 500) -> SqlExecution:
    """Run one query under every database-level control, returning rows or a safe error."""
    conn = open_read_only(db_path)
    deadline = time.perf_counter() + timeout_s
    ticks = [0]

    def over_budget() -> int:
        ticks[0] += 1
        return 1 if time.perf_counter() > deadline or ticks[0] * _PROGRESS_INTERVAL > MAX_VM_STEPS else 0

    conn.set_progress_handler(over_budget, _PROGRESS_INTERVAL)
    started = time.perf_counter()
    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        kept, truncated = _bounded_rows(cursor, max_rows)
        non_finite = sum(1 for row in kept for v in row if isinstance(v, float) and not math.isfinite(v))
        rows = [[_json_safe(v) for v in row] for row in kept]
        return SqlExecution(status="ok", columns=columns, rows=rows, row_count=len(rows),
                            truncated=truncated, non_finite_values=non_finite,
                            duration_ms=_ms(started))
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            return SqlExecution(status="timeout", duration_ms=_ms(started),
                                error=f"The query exceeded the {timeout_s:g}-second time limit and was stopped.")
        return SqlExecution(status="error", duration_ms=_ms(started), error=_safe_error(exc))
    except sqlite3.Error as exc:
        return SqlExecution(status="error", duration_ms=_ms(started), error=_safe_error(exc))
    finally:
        conn.close()


def _bounded_rows(cursor: sqlite3.Cursor, max_rows: int) -> tuple[list[tuple], bool]:
    """Rows up to the row cap and the total size cap, and whether anything was left out."""
    kept: list[tuple] = []
    size = 0
    for row in cursor:
        if len(kept) == max_rows:
            return kept, True
        size += sum(len(v) if isinstance(v, (str, bytes)) else 8 for v in row)
        if size > MAX_RESULT_BYTES:
            return kept, True
        kept.append(row)
    return kept, False


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if arg1 in ALLOWED_TABLES else sqlite3.SQLITE_DENY
    if action == _SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def _safe_error(exc: sqlite3.Error) -> str:
    text = str(exc)
    if any(marker in text for marker in ("not authorized", "prohibited", "readonly", "query_only")):
        return NOT_ALLOWED_MESSAGE
    return f"SQLite error: {text}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<binary, {len(value)} bytes>"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
