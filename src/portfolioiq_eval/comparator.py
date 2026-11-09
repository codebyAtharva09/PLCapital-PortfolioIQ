"""Execution-based result comparison: rules B7, B12 and B13 in docs/BUSINESS_RULES.md.

Results are compared by their values, never by SQL text.

- Columns are matched by values; names and aliases are ignored.
- Strict accuracy (B13): the candidate has exactly as many columns as the reference and every
  reference column is matched. Relaxed accuracy also accepts extra candidate columns.
- Row order (B7) matters only when the reference SQL combines ORDER BY with LIMIT (a top-N
  question); otherwise rows are compared as a multiset.
- Numbers (B12): integers must match exactly; other numbers within 0.005 absolute or 1e-6
  relative. There is no unit or scale conversion, and text is never parsed into numbers.
- An empty reference result is matched by any empty candidate result.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot

MAX_COLUMN_MAPPINGS = 5_000


@dataclass(frozen=True)
class Comparison:
    strict: bool
    relaxed: bool
    ordered: bool
    reason: str


def requires_order(reference_sql: str) -> bool:
    """True for top-N questions: the reference SQL has both ORDER BY and LIMIT at its top level."""
    tree = sqlglot.parse_one(reference_sql, read="sqlite")
    return bool(tree.args.get("order")) and bool(tree.args.get("limit"))


def values_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a.strip() == b.strip()
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if _is_int(a) or _is_int(b):
            return a == b
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=0.005)
    return a == b


def compare_results(reference_columns: Sequence[str], reference_rows: Sequence[Sequence[Any]],
                    candidate_columns: Sequence[str], candidate_rows: Sequence[Sequence[Any]], *,
                    ordered: bool) -> Comparison:
    if not reference_rows:
        empty = not candidate_rows
        return Comparison(empty, empty, ordered,
                          "both results are empty" if empty else f"expected no rows, got {len(candidate_rows)}")
    if len(candidate_rows) != len(reference_rows):
        return _fail(ordered, f"{len(candidate_rows)} row(s) instead of {len(reference_rows)}")

    reference_by_column = list(zip(*reference_rows))
    candidate_by_column = list(zip(*candidate_rows))
    options = []
    for index, column in enumerate(reference_by_column):
        matches = [k for k, other in enumerate(candidate_by_column) if _same_values(column, other)]
        if not matches:
            return _fail(ordered, f"no column matches the expected values of '{reference_columns[index]}'")
        options.append(matches)

    for count, mapping in enumerate(itertools.product(*options)):
        if count >= MAX_COLUMN_MAPPINGS:
            break
        if len(set(mapping)) != len(mapping):
            continue
        projected = [tuple(row[k] for k in mapping) for row in candidate_rows]
        if _rows_match(reference_rows, projected, ordered):
            extra = len(candidate_columns) - len(reference_columns)
            return Comparison(extra == 0, True, ordered,
                              "match" if extra == 0 else f"match with {extra} extra column(s)")
    return _fail(ordered, "the rows differ" + (" (order matters for this top-N question)" if ordered else ""))


def _rows_match(reference: Sequence[Sequence[Any]], candidate: Sequence[Sequence[Any]], ordered: bool) -> bool:
    if ordered:
        return all(_row_equal(r, c) for r, c in zip(reference, candidate))
    remaining = list(candidate)
    for row in reference:
        for index, other in enumerate(remaining):
            if _row_equal(row, other):
                del remaining[index]
                break
        else:
            return False
    return True


def _row_equal(a: Sequence[Any], b: Sequence[Any]) -> bool:
    return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))


def _same_values(a: Sequence[Any], b: Sequence[Any]) -> bool:
    return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(sorted(a, key=_key), sorted(b, key=_key)))


def _key(value: Any) -> tuple:
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, (int, float)):
        return (1, float(value), "")
    return (2, 0.0, str(value).strip())


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fail(ordered: bool, reason: str) -> Comparison:
    return Comparison(False, False, ordered, reason)
