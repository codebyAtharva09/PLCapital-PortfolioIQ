"""Additional static rules for SQL written by the model, applied on top of the frozen guard in sql_guard.py.

sql_guard.py is part of the frozen baseline and is left byte-for-byte as recorded. These rules only ever add rejections: a query
passes here only if ``validate_sql`` already accepted it, and the same string that is checked is the string that runs. SQLite's own
authorizer and limits (sql_engine.py) still apply afterwards, so this layer explains refusals early rather than being the only defence.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from portfolioiq.sql_engine import ALLOWED_FUNCTIONS
from portfolioiq.sql_guard import SqlValidation, _reject, validate_sql

MAX_AST_NODES = 3_000  # far above any analytic query here; bounds validation work and very long expressions
_STRUCTURAL = (exp.And, exp.Or, exp.Xor, exp.Case, exp.If, exp.Cast, exp.TryCast, exp.Exists, exp.Any, exp.All)  # syntax, not callable functions
_FUNCTION_NAME = re.compile(r"(\w+)\(")
TOO_DEEP = "The query is nested too deeply to validate."


def validate_generated_sql(sql: str | None, schema: dict[str, dict[str, str]]) -> SqlValidation:
    """The frozen guard's verdict, tightened. Never raises on hostile input."""
    try:
        result = validate_sql(sql, schema)
        if not result.ok:
            return result
        tree = sqlglot.parse_one(sql, read="sqlite")
        return _tightened(tree) or result
    except RecursionError:
        return _reject("too_long", TOO_DEEP)
    except SqlglotError as exc:
        return _reject("parse_error", f"The SQL could not be analysed: {type(exc).__name__}")


def _tightened(tree: exp.Expression) -> SqlValidation | None:
    if sum(1 for _ in tree.walk()) > MAX_AST_NODES:
        return _reject("too_long", "The query is too complex to validate.")
    if any(with_.args.get("recursive") for with_ in tree.find_all(exp.With)):
        return _reject("forbidden_operation", "Recursive CTEs are not allowed.")
    names = [cte.alias_or_name for cte in tree.find_all(exp.CTE)] + [table.name for table in tree.find_all(exp.Table)]
    system = next((name for name in names if name.lower().startswith(("sqlite_", "pragma_"))), None)  # a CTE cannot borrow a system table's name
    if system:
        return _reject("forbidden_table", f"System table {system} is not allowed.")
    for function in tree.find_all(exp.Func):  # SQLGlot gives some functions their own node type; those meet the same allowlist
        if isinstance(function, (exp.Anonymous, *_STRUCTURAL)):
            continue
        match = _FUNCTION_NAME.match(function.sql(dialect="sqlite"))
        if match and match.group(1).lower() not in ALLOWED_FUNCTIONS:
            return _reject("forbidden_function", f"Function {match.group(1)}() is not allowed.")
    return None
