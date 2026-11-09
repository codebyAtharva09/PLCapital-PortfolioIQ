"""Static validation of generated SQL with SQLGlot (SQLite dialect).

The first of two independent layers. It explains rejections clearly and records which tables a
query reads. SQLite itself enforces the same limits again at execution (sql_engine.py), so a
parser disagreement between SQLGlot and SQLite cannot open a gap.
"""

from __future__ import annotations

import re
from typing import Literal

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError, TokenError
from sqlglot.optimizer.qualify import qualify

from portfolioiq.sql_engine import ALLOWED_FUNCTIONS

MAX_SQL_LENGTH = 10_000
_UNRESOLVED = re.compile(r"Column '([\w.\"]+)' could not be resolved|Unknown column: ([\w.\"]+)")
_FORBIDDEN = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma", "Attach", "Detach",
        "Transaction", "Commit", "Rollback", "Merge", "Into", "Analyze", "Use", "Set", "Copy", "Kill",
        "Describe", "Show",
    ) if hasattr(exp, name)
)
Category = Literal[
    "ok", "empty", "too_long", "parse_error", "multiple_statements", "not_select", "forbidden_operation",
    "forbidden_table", "qualified_name", "forbidden_function", "unknown_table", "unknown_column",
]
# Categories that mean the request itself is unsafe. These are rejected outright, never "repaired".
SECURITY_CATEGORIES = frozenset({
    "multiple_statements", "not_select", "forbidden_operation", "forbidden_table", "qualified_name",
    "forbidden_function",
})


class SqlValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    category: Category
    reason: str | None = None
    statement_type: str | None = None
    tables: list[str] = []


def validate_sql(sql: str | None, schema: dict[str, dict[str, str]]) -> SqlValidation:
    """Accept exactly one SELECT (or WITH ... SELECT) over the supplied tables and columns."""
    text = (sql or "").strip()
    if not text:
        return _reject("empty", "No SQL was produced.")
    if len(text) > MAX_SQL_LENGTH:
        return _reject("too_long", f"The SQL is longer than {MAX_SQL_LENGTH} characters.")
    try:
        trees = [t for t in sqlglot.parse(text, read="sqlite") if t is not None]
    except (ParseError, TokenError) as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else "syntax error"
        return _reject("parse_error", f"The SQL could not be parsed: {first_line}")
    if not trees:
        return _reject("empty", "No SQL statement was found.")
    if len(trees) > 1:
        return _reject("multiple_statements", "Only one statement is allowed per query.")
    tree = trees[0]

    forbidden = next((node for node in tree.walk() if isinstance(node, _FORBIDDEN)), None)
    if forbidden is not None:
        name = type(forbidden).__name__.upper()
        return _reject("forbidden_operation", f"{name} is not allowed; only read-only SELECT queries can run.")
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return _reject("not_select", "Only a single SELECT (or WITH ... SELECT) statement is allowed.")

    tables_lower = {t.lower() for t in schema}
    columns_lower = {c.lower() for cols in schema.values() for c in cols}
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    read_tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func):  # a table-valued function such as pragma_table_info(...)
            function_name = table.this.name or type(table.this).__name__
            category = "forbidden_table" if function_name.lower().startswith("pragma_") else "forbidden_function"
            return _reject(category, f"Table-valued function {function_name}() is not allowed.")
        if table.args.get("db") or table.args.get("catalog"):
            return _reject("qualified_name", f"Qualified table names are not allowed: {table.sql(dialect='sqlite')}")
        name = table.name.lower()
        if name in cte_names:
            continue
        if name.startswith(("sqlite_", "pragma_")):
            return _reject("forbidden_table", f"System table {table.name} is not allowed.")
        if name not in tables_lower:
            return _reject("unknown_table", f"Unknown table: {table.name}")
        read_tables.add(name)

    for function in tree.find_all(exp.Anonymous):
        if function.name.lower() not in ALLOWED_FUNCTIONS:
            return _reject("forbidden_function", f"Function {function.name}() is not allowed.")

    defined = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    for table_alias in tree.find_all(exp.TableAlias):
        defined |= {c.name.lower() for c in table_alias.columns}
    qualifiers = cte_names | {t.alias_or_name.lower() for t in tree.find_all(exp.Table)} | {
        t.name.lower() for t in tree.find_all(exp.Table)} | {
        s.alias_or_name.lower() for s in tree.find_all(exp.Subquery) if s.alias_or_name}
    for column in tree.find_all(exp.Column):
        if column.table and column.table.lower() not in qualifiers:
            return _reject("unknown_table", f"Unknown table or alias: {column.table}")
        if isinstance(column.this, exp.Star):
            continue
        if column.name.lower() not in columns_lower | defined:
            return _reject("unknown_column", f"Unknown column: {column.name}")

    misplaced = _misplaced_column(tree, schema)
    if misplaced:
        return _reject("unknown_column", f"Column not available from the tables in this query: {misplaced}")

    return SqlValidation(ok=True, category="ok", statement_type=type(tree).__name__.upper(),
                         tables=sorted(read_tables))


def _misplaced_column(tree: exp.Expression, schema: dict[str, dict[str, str]]) -> str | None:
    """A column that exists somewhere in the schema but not in the tables this query reads, resolved per scope.

    Aliases, joins, CTEs and subqueries are resolved by SQLGlot's qualifier. Anything else it cannot process is left to the
    checks above and to SQLite, so this can only add rejections.
    """
    lowered = {t.lower(): {c.lower(): d for c, d in cols.items()} for t, cols in schema.items()}
    try:
        qualify(tree.copy(), schema=lowered, dialect="sqlite", quote_identifiers=False, identify=False, validate_qualify_columns=True)
    except OptimizeError as exc:
        match = _UNRESOLVED.search(str(exc))
        return (match.group(1) or match.group(2)) if match else None
    except (SqlglotError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


def _reject(category: Category, reason: str) -> SqlValidation:
    return SqlValidation(ok=False, category=category, reason=reason)
