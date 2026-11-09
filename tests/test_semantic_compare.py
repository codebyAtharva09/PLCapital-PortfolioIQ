"""The supplemental comparison explains a result; the official comparator alone decides the score."""

from portfolioiq_eval.comparator import compare_results
from portfolioiq_eval.semantic_compare import diagnose

COLS = ["portfolio_name", "tech_value", "total_value"]
ROWS = [["A", 10.0, 100.0], ["B", 20.0, 80.0]]


def test_exact_shape():
    result = diagnose(COLS, ROWS, COLS, ROWS, ordered=False)
    assert result["category"] == "exact_shape_match" and result["official_strict"] and result["semantic_match"]


def test_columns_are_aligned_by_name_when_their_order_differs():
    got = ["total_value", "portfolio_name", "tech_value"]
    rows = [[r[2], r[0], r[1]] for r in ROWS]
    result = diagnose(COLS, ROWS, got, rows, ordered=False)
    assert result["semantic_match"] and result["alignment"]["total_value"] == "name" and not result["missing_columns"]


def test_a_renamed_column_is_aligned_by_values():
    result = diagnose(COLS, ROWS, ["portfolio_name", "tv", "denom"], ROWS, ordered=False)
    assert result["alignment"]["tech_value"] == "values" and result["semantic_match"]


def test_a_missing_column_is_named():
    result = diagnose(COLS, ROWS, COLS[:2], [r[:2] for r in ROWS], ordered=False)
    assert result["category"] == "missing_column" and result["missing_columns"] == ["total_value"] and not result["semantic_match"]


def test_an_extra_column_is_a_warning_recorded_separately_from_the_official_failure():
    result = diagnose(COLS, ROWS, [*COLS, "note"], [[*r, "x"] for r in ROWS], ordered=False)
    assert result["category"] == "semantic_value_match" and result["extra_columns"] == ["note"]
    assert result["semantic_match"] and not result["official_strict"]


def test_duplicate_columns_are_detected():
    result = diagnose(COLS, ROWS, [*COLS, "tech_value"], [[*r, r[1]] for r in ROWS], ordered=False)
    assert result["duplicate_columns"] == ["tech_value"]


def test_ordering_is_distinguished_from_wrong_data():
    result = diagnose(COLS, ROWS, COLS, list(reversed(ROWS)), ordered=True)
    assert result["category"] == "ordering_mismatch" and result["ordering_mismatch"] and not result["wrong_value_columns"]
    assert diagnose(COLS, ROWS, COLS, list(reversed(ROWS)), ordered=False)["category"] == "exact_shape_match"


def test_wrong_values_are_named():
    bad = [["A", 11.0, 100.0], ["B", 20.0, 80.0]]
    result = diagnose(COLS, ROWS, COLS, bad, ordered=False)
    assert result["category"] == "data_value_mismatch" and result["wrong_value_columns"] == ["tech_value"]


def test_a_row_count_difference_is_never_a_semantic_match():
    result = diagnose(COLS, ROWS, COLS, [*ROWS, ["C", 0.0, 5.0]], ordered=False)
    assert result["category"] == "row_count_mismatch" and not result["semantic_match"]


def test_the_official_verdict_inside_the_diagnosis_is_the_frozen_comparators():
    got, rows = [*COLS, "note"], [[*r, "x"] for r in ROWS]
    official = compare_results(COLS, ROWS, got, rows, ordered=False)
    result = diagnose(COLS, ROWS, got, rows, ordered=False)
    assert (result["official_strict"], result["official_relaxed"]) == (official.strict, official.relaxed)
