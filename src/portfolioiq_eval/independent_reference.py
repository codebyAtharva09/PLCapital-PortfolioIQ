"""Reference calculations written from the assessment's definitions, in pandas, over the supplied CSV files.

This module imports nothing from PortfolioIQ's runtime package and uses no SQL, no SQLite and no model output, so a defect in the
production query path cannot be repeated here by sharing code. It answers two questions: what a figure should be, and whether a
production result agrees with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

TABLES = ("portfolios", "securities", "sectors", "holdings", "transactions", "historical_prices", "portfolio_performance", "risk_metrics")
MONEY_TOLERANCE = 0.005  # the evaluation's numeric tolerance; figures are commonly shown to two decimals
RATIO_TOLERANCE = 0.0006  # a ratio is commonly shown to three decimals


def load(data_dir: Path) -> dict[str, pd.DataFrame]:
    return {name: pd.read_csv(data_dir / f"{name}.csv") for name in TABLES}


def _positions(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frame = data["holdings"].merge(data["securities"], on="security_id", how="left", validate="m:1", suffixes=("", "_sec"))
    frame = frame.merge(data["portfolios"], on="portfolio_id", how="left", validate="m:1", suffixes=("", "_pf"))
    return frame.merge(data["sectors"], on="sector_id", how="left", validate="m:1", suffixes=("", "_sector"))


def diversification(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per portfolio: distinct sectors among stock holdings, all holdings including bonds, and their ratio."""
    positions = _positions(data)
    stocks = positions[positions["asset_type"] == "Stock"]
    sectors = stocks.groupby("portfolio_name")["sector_id"].nunique()
    total = positions.groupby("portfolio_name").size()
    frame = pd.DataFrame({"total_holdings": total})
    frame["distinct_stock_sectors"] = sectors.reindex(frame.index, fill_value=0).astype(int)
    frame["ratio"] = frame["distinct_stock_sectors"] / frame["total_holdings"]
    return frame.reset_index()


@dataclass(frozen=True)
class ExposureReference:
    portfolio: str
    method_a: dict[str, float]
    method_b: dict[str, float]
    stock_weight: float
    bond_weight: float
    unclassified_weight: float
    total_weight: float
    residual: float
    problems: tuple[str, ...]


def exposure(data: dict[str, pd.DataFrame], portfolio: str) -> ExposureReference:
    """Method A: share of the whole portfolio. Method B: share of all stock weight, unclassified stocks staying in the denominator."""
    held = _positions(data)
    held = held[held["portfolio_name"] == portfolio]
    problems = []
    if held["current_weight"].isna().any():
        problems.append("missing weight")
    if ((held["current_weight"] < 0) | (held["current_weight"] > 1)).any():
        problems.append("weight outside 0 to 1")
    if held.duplicated(["portfolio_id", "security_id"]).any():
        problems.append("duplicate holding")
    stocks = held[held["asset_type"] == "Stock"]
    named = stocks[stocks["sector_name"].notna()]
    method_a = {sector: float(weight) for sector, weight in named.groupby("sector_name")["current_weight"].sum().items()}
    stock_weight = float(stocks["current_weight"].sum())
    method_b = {sector: weight / stock_weight for sector, weight in method_a.items()} if stock_weight > 0 else {}
    total = float(held["current_weight"].sum())
    return ExposureReference(portfolio, method_a, method_b, stock_weight, float(held.loc[held["asset_type"] == "Bond", "current_weight"].sum()),
                             float(stocks.loc[stocks["sector_name"].isna(), "current_weight"].sum()), total, 1.0 - total, tuple(problems))


def latest(frame: pd.DataFrame, date_column: str) -> pd.Timestamp:
    return pd.to_datetime(frame[date_column]).max()


def _rows(frame: pd.DataFrame) -> list[list[Any]]:
    return [[value.item() if hasattr(value, "item") else value for value in row] for row in frame.itertuples(index=False, name=None)]


def official_references(data: dict[str, pd.DataFrame]) -> dict[str, dict[str, Any]]:
    """One entry per question: the expected rows, the comparison rule, whether the rows contain a calculation, and how it was built."""
    portfolios, securities, sectors, transactions = data["portfolios"], data["securities"], data["sectors"], data["transactions"]
    positions = _positions(data)
    positions["market_value"] = positions["quantity"] * positions["current_price"]
    classified = securities.merge(sectors, on="sector_id")
    out: dict[str, dict[str, Any]] = {}

    def add(case: str, rows: list[list[Any]], kind: str, note: str, *, ordered: bool = False, tolerance: float = MONEY_TOLERANCE) -> None:
        out[case] = {"rows": rows, "kind": kind, "note": note, "ordered": ordered, "tolerance": tolerance}

    add("Q1", [[len(portfolios)]], "calculated", "Row count of portfolios.csv.")
    add("Q2", _rows(portfolios.loc[portfolios["status"] == "Active", ["portfolio_name"]]), "lookup", "Filter status == Active.")
    add("Q3", _rows(classified.loc[classified["sector_name"] == "Technology", ["symbol", "company_name"]]), "lookup", "Securities joined to sectors, filter Technology.")
    add("Q4", [[float(portfolios.loc[portfolios["target_risk_level"] == "High", "total_aum"].sum())]], "calculated", "Sum of stated total_aum where risk is High.")
    growth = positions[positions["portfolio_name"] == "Growth Equity Fund"].sort_values("cost_basis", ascending=False).head(5)
    add("Q5", _rows(growth[["holding_id", "symbol", "company_name", "quantity", "cost_basis"]]), "calculated", "Growth Equity Fund holdings sorted by cost_basis, first five.", ordered=True)
    average = classified.groupby("sector_name")["current_price"].mean().reset_index()
    add("Q6", _rows(average), "calculated", "Mean of current_price per sector, stocks with a sector only.")
    total_value = positions.groupby("portfolio_name")["market_value"].sum()
    tech_value = positions[positions["sector_name"] == "Technology"].groupby("portfolio_name")["market_value"].sum()
    add("Q7", [[name, float(value), float(total_value[name]), float(value / total_value[name] * 100)] for name, value in tech_value.items()], "calculated",
        "quantity x current_price per holding. The denominator includes bonds. Portfolios with no Technology holding are not listed.")
    ratio = diversification(data)
    selected = ratio[ratio["distinct_stock_sectors"] > 5]
    add("Q8", _rows(selected[["portfolio_name", "distinct_stock_sectors", "total_holdings", "ratio"]]), "calculated", "Distinct stock sectors / all holdings including bonds; more than five sectors.", tolerance=RATIO_TOLERANCE)

    add("U1", _rows(positions.loc[positions["symbol"] == "NVDA", ["portfolio_name"]].drop_duplicates()), "lookup", "Portfolios holding NVDA.")
    add("U2", [[int((positions["portfolio_name"] == "Dividend Aristocrats Fund").sum())]], "calculated", "Row count of that fund's holdings.")
    add("U3", [[float(positions.loc[positions["asset_type"] == "Bond", "market_value"].sum())]], "calculated", "quantity x current_price, bonds only.")
    bonds = securities.loc[securities["asset_type"] == "Bond", ["symbol", "coupon_rate", "maturity_date"]]
    add("U4", _rows(bonds), "lookup", "Bond rows from securities.csv.")
    risk = data["risk_metrics"].merge(portfolios, on="portfolio_id")
    risk = risk[(risk["portfolio_name"] == "Conservative Income Fund") & (pd.to_datetime(risk["calculation_date"]) == latest(data["risk_metrics"], "calculation_date"))]
    add("U5", _rows(risk[["beta"]]), "stored", "Stored beta at the latest calculation date. Retrieval is checked; the source has no return series to recompute beta.")
    anchor = latest(transactions, "transaction_date")
    dates = pd.to_datetime(transactions["transaction_date"])
    window = (dates >= anchor - pd.Timedelta(days=6)) & (dates <= anchor)
    add("U6", [[int(((transactions["transaction_type"] == "BUY") & window).sum())]], "calculated", f"BUY rows in the seven days ending at the dataset's latest transaction date, {anchor.date()}.")
    performed = set(data["portfolio_performance"]["portfolio_id"])
    add("U7", _rows(portfolios.loc[~portfolios["portfolio_id"].isin(performed), ["portfolio_name"]]), "lookup", "Portfolios absent from portfolio_performance.csv.")
    add("U8", [[float(positions.loc[positions["symbol"] == "AAPL", "purchase_price"].mean())]], "calculated", "Mean purchase_price of AAPL holdings.")
    last_year = dates.dt.year == anchor.year - 1
    add("U9", [] if not last_year.any() else _rows(transactions.loc[last_year]), "lookup", f"Transactions in {anchor.year - 1}, the calendar year before the dataset's latest date.")
    counts = classified.groupby("sector_name").size()
    add("U10", [[counts.idxmax()]], "calculated", f"Sector with the most securities ({int(counts.max())}).")
    return out


def compare(actual: list[list[Any]], expected: list[list[Any]], *, ordered: bool = False, tolerance: float = MONEY_TOLERANCE) -> tuple[bool, float | None, str]:
    """(agrees, largest numeric difference, explanation). Row order counts only when the question fixes it."""
    def text_key(row: list[Any]) -> list[str]:
        return [str(v) for v in row if not isinstance(v, (int, float))] or [f"{v:.6f}" for v in row]

    if len(actual) != len(expected):
        return False, None, f"{len(actual)} row(s) returned, {len(expected)} expected"
    if not ordered:
        actual, expected = sorted(actual, key=text_key), sorted(expected, key=text_key)
    worst = 0.0
    for got, want in zip(actual, expected):
        if len(got) != len(want):
            return False, None, f"{len(got)} column(s) returned, {len(want)} expected"
        for a, b in zip(got, want):
            numeric = isinstance(a, (int, float)) and isinstance(b, (int, float))
            if numeric:
                if abs(a - b) > tolerance:
                    return False, abs(a - b), f"{a} differs from {b}"
                worst = max(worst, abs(a - b))
            elif not ((pd.isna(a) and pd.isna(b)) or a == b):
                return False, None, f"{a!r} differs from {b!r}"
    return True, worst, "agrees"
