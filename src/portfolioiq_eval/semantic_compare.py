"""Supplemental diagnostic comparison of a SQL result with its reference. It is never the official score.

The official comparator (comparator.py) is frozen and decides PASS or FAIL. This module explains a result by aligning
columns by name first and by values second, and it separates what the official score merges: exact shape, missing columns,
extra columns, duplicate columns, ordering and wrong data. Its output is a diagnosis, reported beside the official score.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from portfolioiq.config import Settings
from portfolioiq.sql_engine import ensure_reference_db, execute_sql
from portfolioiq_eval import sql_eval
from portfolioiq_eval.comparator import compare_results, requires_order, values_equal


def _tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower())) - {"avg", "total", "sum"}


def _column(rows: Sequence[Sequence[Any]], index: int) -> list[Any]:
    return [row[index] for row in rows]


def _same_multiset(left: list[Any], right: list[Any]) -> bool:
    remaining = list(right)
    for value in left:
        match = next((i for i, other in enumerate(remaining) if values_equal(value, other)), None)
        if match is None:
            return False
        del remaining[match]
    return not remaining


def align_columns(ref_cols: Sequence[str], ref_rows: Sequence[Sequence[Any]], got_cols: Sequence[str],
                  got_rows: Sequence[Sequence[Any]]) -> dict[str, tuple[int, str] | None]:
    """Each reference column's position in the result: same name, then shared name words, then same values."""
    used: set[int] = set()
    mapping: dict[str, tuple[int, str] | None] = {name: None for name in ref_cols}

    def claim(reference: str, index: int, how: str) -> None:
        mapping[reference] = (index, how)
        used.add(index)

    for reference in ref_cols:
        for index, name in enumerate(got_cols):
            if index not in used and name.lower() == reference.lower():
                claim(reference, index, "name")
                break
    for reference in ref_cols:
        if mapping[reference] is None:
            for index, name in enumerate(got_cols):
                if index not in used and _tokens(reference) and _tokens(reference) <= _tokens(name):
                    claim(reference, index, "name words")
                    break
    if len(ref_rows) == len(got_rows):
        for position, reference in enumerate(ref_cols):
            if mapping[reference] is None:
                for index in range(len(got_cols)):
                    if index not in used and _same_multiset(_column(ref_rows, position), _column(got_rows, index)):
                        claim(reference, index, "values")
                        break
    return mapping


def diagnose(ref_cols: Sequence[str], ref_rows: Sequence[Sequence[Any]], got_cols: Sequence[str],
             got_rows: Sequence[Sequence[Any]], *, ordered: bool) -> dict:
    """What matches and what does not, each named separately."""
    official = compare_results(ref_cols, ref_rows, got_cols, got_rows, ordered=ordered)
    mapping = align_columns(ref_cols, ref_rows, got_cols, got_rows)
    missing = [name for name, found in mapping.items() if found is None]
    placed = {found[0] for found in mapping.values() if found}
    extra = [name for index, name in enumerate(got_cols) if index not in placed]
    lowered = [name.lower() for name in got_cols]
    duplicates = sorted({name for name in lowered if lowered.count(name) > 1})
    same_row_count = len(ref_rows) == len(got_rows)
    wrong_values: list[str] = []
    ordering_mismatch = False
    if same_row_count:
        for position, name in enumerate(ref_cols):
            found = mapping[name]
            if found is None:
                continue
            reference, got = _column(ref_rows, position), _column(got_rows, found[0])
            if not _same_multiset(reference, got):
                wrong_values.append(name)
            elif ordered and not all(values_equal(a, b) for a, b in zip(reference, got)):
                ordering_mismatch = True
    semantic = same_row_count and (not ref_rows or (not missing and not wrong_values and not ordering_mismatch))
    category = ("exact_shape_match" if official.strict else
                "semantic_value_match" if semantic else
                "row_count_mismatch" if not same_row_count else
                "missing_column" if missing else
                "data_value_mismatch" if wrong_values else
                "ordering_mismatch")
    return {"official_strict": official.strict, "official_relaxed": official.relaxed, "official_reason": official.reason,
            "category": category, "semantic_match": semantic, "missing_columns": missing, "extra_columns": extra,
            "duplicate_columns": duplicates, "wrong_value_columns": wrong_values, "ordering_mismatch": ordering_mismatch,
            "rows": {"reference": len(ref_rows), "result": len(got_rows)},
            "alignment": {name: found[1] if found else None for name, found in mapping.items()}}


def run(settings: Settings, runs_dir: Path, reports_dir: Path, label: str) -> dict:
    """Diagnose every recorded SQL result in a runs folder. Makes no API calls and writes only into ``reports_dir``."""
    db_path = ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir)
    latest: dict[str, dict] = {}
    for path in sql_eval.recorded_outputs(runs_dir):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if (record.get("result") or {}).get("execution"):
                    latest[record["case"]["id"]] = record["result"]
    items = []
    for case in sql_eval.load_cases():
        reference_sql = getattr(case, "reference_sql", None)
        if not reference_sql or case.id not in latest or latest[case.id]["execution"].get("status") != "ok":
            continue
        reference = execute_sql(db_path, reference_sql, max_rows=10_000)
        execution = latest[case.id]["execution"]
        items.append({"id": case.id, **diagnose(reference.columns, reference.rows, execution["columns"], execution["rows"],
                                                ordered=requires_order(reference_sql))})
    report = {"label": label, "note": "Supplemental diagnosis only. The official score comes from comparator.py and is not changed by this.",
              "exact_shape": sum(i["official_strict"] for i in items), "semantic": sum(i["semantic_match"] for i in items),
              "evaluated": len(items), "items": items}
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "supplemental_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"# Supplemental diagnostic comparison: {label}", "", report["note"], "",
             f"Exact shape {report['exact_shape']} of {report['evaluated']}; semantic value match {report['semantic']} of {report['evaluated']}.", "",
             "| ID | Category | Rows (ref / result) | Missing | Extra | Wrong values | Order |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {i['id']} | {i['category']} | {i['rows']['reference']} / {i['rows']['result']} | {', '.join(i['missing_columns']) or '-'} | "
              f"{', '.join(i['extra_columns']) or '-'} | {', '.join(i['wrong_value_columns']) or '-'} | {'differs' if i['ordering_mismatch'] else '-'} |" for i in items]
    (reports_dir / "supplemental_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
