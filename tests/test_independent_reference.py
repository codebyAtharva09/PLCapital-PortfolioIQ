"""The pandas reference: its independence, the diversification definition, exposure semantics, and rejection of corrupted output."""

import ast
from pathlib import Path

import pandas as pd
import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq_eval import independent_reference as ref

SOURCE = PROJECT_ROOT / "src" / "portfolioiq_eval" / "independent_reference.py"


def frames(holdings, securities, portfolios=("P",)):
    return {
        "portfolios": pd.DataFrame({"portfolio_id": range(1, len(portfolios) + 1), "portfolio_name": list(portfolios)}),
        "sectors": pd.DataFrame({"sector_id": [1, 2, 3], "sector_name": ["Tech", "Energy", "Health"]}),
        "securities": pd.DataFrame(securities, columns=["security_id", "asset_type", "sector_id"]),
        "holdings": pd.DataFrame(holdings, columns=["holding_id", "portfolio_id", "security_id", "current_weight"]),
    }


STOCK_TECH, STOCK_TECH_2, STOCK_ENERGY, BOND, UNCLASSIFIED = (1, "Stock", 1), (2, "Stock", 1), (3, "Stock", 2), (4, "Bond", None), (5, "Stock", None)
SECURITIES = [STOCK_TECH, STOCK_TECH_2, STOCK_ENERGY, BOND, UNCLASSIFIED]


def only(frame):
    return frame.iloc[0]


def test_the_reference_shares_no_code_with_the_runtime_package():
    imported = {node.module for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))) if isinstance(node, ast.ImportFrom)}
    imported |= {a.name for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))) if isinstance(node, ast.Import) for a in node.names}
    assert not {name for name in imported if name and name.startswith("portfolioiq")} and "sqlite3" not in imported and "sqlglot" not in imported


def test_the_documented_fund_is_rebuilt_from_the_csv_files():
    table = ref.diversification(ref.load(PROJECT_ROOT / "data")).set_index("portfolio_name")
    fund = table.loc["Total Stock Market Index Fund"]
    assert (fund["distinct_stock_sectors"], fund["total_holdings"], fund["ratio"]) == (6, 12, 0.5)
    assert list(table.index[table["distinct_stock_sectors"] > 5]) == ["Total Stock Market Index Fund"]


def test_bonds_count_in_the_denominator_but_not_as_sectors():
    row = only(ref.diversification(frames([(1, 1, 1, .5), (2, 1, 4, .5)], SECURITIES)))
    assert (row["distinct_stock_sectors"], row["total_holdings"], row["ratio"]) == (1, 2, .5)


def test_a_bond_with_a_sector_is_still_not_a_stock_sector():
    securities = [STOCK_TECH, (4, "Bond", 2)]
    assert only(ref.diversification(frames([(1, 1, 1, .5), (2, 1, 4, .5)], securities)))["distinct_stock_sectors"] == 1


def test_several_stocks_in_one_sector_count_once():
    assert only(ref.diversification(frames([(1, 1, 1, .4), (2, 1, 2, .3), (3, 1, 3, .3)], SECURITIES)))["distinct_stock_sectors"] == 2


def test_a_stock_with_no_sector_adds_a_holding_but_no_sector():
    row = only(ref.diversification(frames([(1, 1, 1, .5), (2, 1, 5, .5)], SECURITIES)))
    assert (row["distinct_stock_sectors"], row["total_holdings"]) == (1, 2)


def test_no_stock_holdings_gives_zero():
    row = only(ref.diversification(frames([(1, 1, 4, 1.0)], SECURITIES)))
    assert (row["distinct_stock_sectors"], row["ratio"]) == (0, 0.0)


def test_a_portfolio_with_zero_holdings_has_no_row_rather_than_a_division_by_zero():
    assert ref.diversification(frames([(1, 1, 1, 1.0)], SECURITIES, ("P", "Empty")))["portfolio_name"].tolist() == ["P"]


def test_duplicates_are_flagged_and_invalid_weights_do_not_pass():
    assert "duplicate holding" in ref.exposure(frames([(1, 1, 1, .5), (2, 1, 1, .5)], SECURITIES), "P").problems
    assert "weight outside 0 to 1" in ref.exposure(frames([(1, 1, 1, 1.5)], SECURITIES), "P").problems
    assert "weight outside 0 to 1" in ref.exposure(frames([(1, 1, 1, -.1)], SECURITIES), "P").problems
    assert "missing weight" in ref.exposure(frames([(1, 1, 1, None), (2, 1, 3, .5)], SECURITIES), "P").problems


def test_methods_bonds_and_residual():
    result = ref.exposure(frames([(1, 1, 1, .3), (2, 1, 3, .3), (3, 1, 4, .3)], SECURITIES), "P")
    assert result.method_a == pytest.approx({"Tech": .3, "Energy": .3})
    assert result.method_b == pytest.approx({"Tech": .5, "Energy": .5})
    assert result.bond_weight == pytest.approx(.3) and result.residual == pytest.approx(.1)


def test_unclassified_stock_stays_in_the_method_b_denominator_and_out_of_the_named_rows():
    result = ref.exposure(frames([(1, 1, 1, .4), (2, 1, 5, .4)], SECURITIES), "P")
    assert set(result.method_a) == {"Tech"} and result.method_b["Tech"] == pytest.approx(.5) and result.unclassified_weight == pytest.approx(.4)


@pytest.mark.parametrize("actual, kwargs", [
    ([["A", 1.02]], {}),
    ([["A", 1.0], ["B", 2.0]], {}),
    ([["Z", 1.0]], {}),
    ([["A"]], {}),
    ([], {}),
], ids=["wrong value", "extra row", "wrong key", "missing column", "no rows"])
def test_corrupted_output_is_rejected(actual, kwargs):
    assert not ref.compare(actual, [["A", 1.0]], **kwargs)[0]


def test_order_counts_only_when_the_question_fixes_it():
    a, b = [["A", 2.0], ["B", 1.0]], [["B", 1.0], ["A", 2.0]]
    assert ref.compare(a, b)[0] and not ref.compare(a, b, ordered=True)[0]


def test_a_rounded_figure_within_tolerance_agrees():
    assert ref.compare([["A", 84.67]], [["A", 84.6667]])[0]
