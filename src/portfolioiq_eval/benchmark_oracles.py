"""Independent expected answers for the independent benchmark, computed straight from the supplied CSV files.

Nothing here uses SQLite, the SQL tool, the exposure engine or the portfolio resolver. Each oracle is plain Python
over the raw rows, so it can disagree with the system under test if the system is wrong. The benchmark's
reference SQL is checked against these oracles in tests/test_independent_benchmark.py, and the frozen expected
values stored in the dataset must equal both.

An oracle is keyed by case id and returns one of:

- SQL cases:      {"columns": [labels], "rows": [[...], ...]}  (labels are descriptive; values are what is compared)
- exposure cases: {"portfolio", "method_a": {sector: share}, "method_b": {...}, "eligible", "bonds", "residual"}
- hybrid cases:   {"sector", "portfolios": [...], "method_a": {portfolio: share}, "leader"}
- clarification:  {"candidates": [...]}
"""

from __future__ import annotations

import csv
import datetime as dt
import math
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from portfolioiq.config import PROJECT_ROOT

DATA = PROJECT_ROOT / "data"
BOND_CANDIDATE_MIN_WEIGHT = 0.5  # rule B5.5: a portfolio at least half bonds is a candidate for "bond"


class Tables:
    """The supplied CSV files as lists of string-valued rows, with a few lookups."""

    def __init__(self, data_dir: Path = DATA):
        def rows(name: str) -> list[dict]:
            with (data_dir / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
                return list(csv.DictReader(stream))

        self.portfolios, self.securities, self.sectors = rows("portfolios"), rows("securities"), rows("sectors")
        self.holdings, self.transactions, self.prices = rows("holdings"), rows("transactions"), rows("historical_prices")
        self.performance, self.risk = rows("portfolio_performance"), rows("risk_metrics")
        self.sector_name = {r["sector_id"]: r["sector_name"] for r in self.sectors}
        self.security_by_id = {r["security_id"]: r for r in self.securities}
        self.security_by_symbol = {r["symbol"]: r for r in self.securities}
        self.portfolio_by_name = {r["portfolio_name"]: r for r in self.portfolios}
        self.portfolio_by_id = {r["portfolio_id"]: r for r in self.portfolios}

    def holdings_of(self, portfolio_name: str) -> list[dict]:
        pid = self.portfolio_by_name[portfolio_name]["portfolio_id"]
        return [h for h in self.holdings if h["portfolio_id"] == pid]

    def sector_of(self, security: dict) -> str | None:
        return self.sector_name.get(security["sector_id"]) if security["sector_id"] else None


def sql_result(columns: list[str], rows: list[list]) -> dict:
    return {"columns": columns, "rows": rows}


def exposure_of(t: Tables, portfolio_name: str) -> dict:
    """Methods A and B from rules B1-B3: stated weights, stocks with a sector only, bonds excluded."""
    holdings = t.holdings_of(portfolio_name)
    by_sector: dict[str, list[float]] = defaultdict(list)
    bonds, everything = [], []
    for h in holdings:
        security, weight = t.security_by_id[h["security_id"]], float(h["current_weight"])
        everything.append(weight)
        if security["asset_type"] == "Bond":
            bonds.append(weight)
        elif security["sector_id"]:
            by_sector[t.sector_of(security)].append(weight)
    method_a = {sector: math.fsum(ws) for sector, ws in by_sector.items()}
    eligible = math.fsum(method_a.values())
    return {"portfolio": portfolio_name, "method_a": method_a,
            "method_b": {s: a / eligible for s, a in method_a.items()} if eligible else {},
            "eligible": eligible, "bonds": math.fsum(bonds), "residual": 1.0 - math.fsum(everything)}


def _hybrid(t: Tables, sector: str, selected: list[str]) -> dict:
    shares = {name: exposure_of(t, name)["method_a"].get(sector, 0.0) for name in selected}
    best = max(shares.values())
    leaders = [n for n, v in shares.items() if abs(v - best) <= 1e-12]
    if len(leaders) != 1:
        raise AssertionError(f"the hybrid benchmark case must have a unique leader, got {leaders}")
    return {"sector": sector, "portfolios": sorted(selected), "method_a": shares, "leader": leaders[0]}


def _names(t: Tables, keep: Callable[[dict], bool]) -> list[str]:
    return [p["portfolio_name"] for p in t.portfolios if keep(p)]


def _holders(t: Tables, symbol: str) -> list[str]:
    sid = t.security_by_symbol[symbol]["security_id"]
    ids = {h["portfolio_id"] for h in t.holdings if h["security_id"] == sid}
    return [p["portfolio_name"] for p in t.portfolios if p["portfolio_id"] in ids]


def _bond_weight(t: Tables, portfolio_name: str) -> float:
    return math.fsum(float(h["current_weight"]) for h in t.holdings_of(portfolio_name)
                     if t.security_by_id[h["security_id"]]["asset_type"] == "Bond")


def _name_contains(t: Tables, word: str) -> list[str]:
    return _names(t, lambda p: word in p["portfolio_name"].lower().split())


def _last_week(t: Tables) -> tuple[str, str]:
    """The previous completed Monday-to-Sunday week before the latest transaction date (rule B10)."""
    anchor = dt.date.fromisoformat(max(r["transaction_date"] for r in t.transactions))
    monday = anchor - dt.timedelta(days=anchor.weekday())
    return str(monday - dt.timedelta(days=7)), str(monday - dt.timedelta(days=1))


def _top_sharpe(t: Tables) -> list:
    best = max(t.performance, key=lambda r: float(r["sharpe_ratio"]))
    ties = [r for r in t.performance if float(r["sharpe_ratio"]) == float(best["sharpe_ratio"])]
    if len(ties) != 1:
        raise AssertionError("the highest Sharpe ratio must be unique for this benchmark case")
    return [float(best["sharpe_ratio"]), t.portfolio_by_id[best["portfolio_id"]]["portfolio_name"], best["performance_date"]]


ORACLES: dict[str, Callable[[Tables], dict]] = {
    # --- development --------------------------------------------------------------------------------
    "D01": lambda t: sql_result(["bond_count"], [[sum(1 for s in t.securities if s["asset_type"] == "Bond")]]),
    "D02": lambda t: sql_result(["risk_level"], [[t.portfolio_by_name["Balanced Portfolio"]["target_risk_level"]]]),
    "D03": lambda t: sql_result(["sector"], [[t.sector_of(next(s for s in t.securities
                                                             if s["company_name"] == "JPMorgan Chase & Co."))]]),
    "D04": lambda t: sql_result(["symbol"], [[s["symbol"]] for s in t.securities if t.sector_of(s) == "Energy"]),
    "D05": lambda t: sql_result(["total_aum"], [[math.fsum(float(p["total_aum"]) for p in t.portfolios
                                                           if p["status"] == "Passive")]]),
    "D06": lambda t: sql_result(["average_aum"], [[math.fsum(float(p["total_aum"]) for p in t.portfolios)
                                                   / len(t.portfolios)]]),
    "D07": lambda t: sql_result(["symbol"], [[t.security_by_id[h["security_id"]]["symbol"]]
                                             for h in t.holdings_of("Dividend Aristocrats Fund")]),
    "D08": lambda t: sql_result(["portfolio_name"], [[n] for n in _holders(t, "PG")]),
    "D09": lambda t: sql_result(["portfolio_name"], [[p["portfolio_name"]] for p in sorted(
        t.portfolios, key=lambda p: -float(p["total_aum"]))[:3]]),
    "D10": lambda t: sql_result(["symbol"], [[max((s for s in t.securities if s["asset_type"] == "Stock"),
                                                   key=lambda s: float(s["current_price"]))["symbol"]]]),
    "D11": lambda t: sql_result(["count"], [[sum(1 for r in t.transactions if r["settlement_date"] > "2023-10-09")]]),
    "D12": lambda t: sql_result(["latest_price_date"], [[max(r["price_date"] for r in t.prices)]]),
    "D13": lambda t: sql_result(["total_cost_basis"], [[math.fsum(float(h["cost_basis"])
                                                                  for h in t.holdings_of("Tech Innovation Fund"))]]),
    "D14": lambda t: sql_result(["market_value"], [[math.fsum(
        float(h["quantity"]) * float(t.security_by_id[h["security_id"]]["current_price"])
        for h in t.holdings_of("Fixed Income Plus"))]]),
    "D15": lambda t: exposure_of(t, "Conservative Income Fund"),
    "D16": lambda t: exposure_of(t, "Emerging Markets Fund"),
    "D17": lambda t: _hybrid(t, "Healthcare", _names(t, lambda p: p["target_risk_level"] == "Low")),
    "D18": lambda t: _hybrid(t, "Technology", _names(t, lambda p: p["status"] == "Passive")),
    "D19": lambda t: {"candidates": _name_contains(t, "index")},
    "D20": lambda t: sql_result(["sortino_ratio"], []),
    "D21": lambda t: {},
    "D22": lambda t: {},
    # --- validation ---------------------------------------------------------------------------------
    "V01": lambda t: sql_result(["portfolio_name"], [[p["portfolio_name"]] for p in t.portfolios if p["status"] == "Passive"]),
    "V02": lambda t: sql_result(["count"], [[sum(1 for s in t.securities if t.sector_of(s) == "Healthcare")]]),
    "V03": lambda t: sql_result(["count"], [[sum(1 for r in t.transactions if r["transaction_type"] == "SELL")]]),
    "V04": lambda t: sql_result(["transaction_type", "quantity"], [[r["transaction_type"], float(r["quantity"])]
                                                                   for r in t.transactions if r["portfolio_id"] == t.portfolio_by_name["Tech Innovation Fund"]["portfolio_id"]]),
    "V05": lambda t: sql_result(["symbol"], [[s["symbol"]] for s in sorted(t.securities, key=lambda s: float(s["current_price"]))[:5]]),
    "V06": lambda t: sql_result(["close_price"], [[float(r["close_price"])] for r in t.prices
                                                  if r["security_id"] == t.security_by_symbol["AAPL"]["security_id"]
                                                  and r["price_date"] == "2023-10-06"]),
    "V07": lambda t: sql_result(["average_beta"], [[math.fsum(float(r["beta"]) for r in t.risk if r["portfolio_id"] == "3")
                                                    / sum(1 for r in t.risk if r["portfolio_id"] == "3")]]),
    "V08": lambda t: exposure_of(t, "Total Stock Market Index Fund"),
    "V09": lambda t: _hybrid(t, "Financials", _names(t, lambda p: p["target_risk_level"] == "Medium")),
    "V10": lambda t: {"candidates": sorted(set(_name_contains(t, "bond")) | set(_names(
        t, lambda p: _bond_weight(t, p["portfolio_name"]) >= BOND_CANDIDATE_MIN_WEIGHT)))},
    "V11": lambda t: sql_result(["count"], [[sum(1 for r in t.transactions if "2021-01-01" <= r["transaction_date"] <= "2021-12-31")]]),
    "V12": lambda t: {},
    "V13": lambda t: {},
    # --- held-out test ------------------------------------------------------------------------------
    "T01": lambda t: sql_result(["creation_date"], [[t.portfolio_by_name["Tech Innovation Fund"]["creation_date"]]]),
    "T02": lambda t: sql_result(["company_name"], [[s["company_name"]] for s in t.securities if t.sector_of(s) == "Consumer Staples"]),
    "T03": lambda t: sql_result(["total_quantity"], [[math.fsum(float(h["quantity"]) for h in t.holdings
                                                                if h["security_id"] == t.security_by_symbol["MSFT"]["security_id"])]]),
    "T04": lambda t: sql_result(["sector"], [[s] for s in sorted({t.sector_of(t.security_by_id[h["security_id"]])
                                                                  for h in t.holdings_of("Balanced Portfolio")
                                                                  if t.security_by_id[h["security_id"]]["asset_type"] == "Stock"})]),
    "T05": lambda t: sql_result(["portfolio_name"], [[max((p for p in t.portfolios if p["target_risk_level"] == "High"),
                                                          key=lambda p: float(p["total_aum"]))["portfolio_name"]]]),
    "T06": lambda t: sql_result(["count"], [[sum(1 for r in t.transactions
                                                  if _last_week(t)[0] <= r["transaction_date"] <= _last_week(t)[1])]]),
    "T07": lambda t: sql_result(["sharpe_ratio", "portfolio_name", "performance_date"], [_top_sharpe(t)]),
    "T08": lambda t: exposure_of(t, "Fixed Income Plus"),
    "T09": lambda t: exposure_of(t, "ESG Sustainable Fund"),
    "T10": lambda t: _hybrid(t, "Energy", _names(t, lambda p: len(t.holdings_of(p["portfolio_name"])) >= 10)),
    "T11": lambda t: {"candidates": _name_contains(t, "international")},
    "T12": lambda t: {},
    "T13": lambda t: {},
    "T14": lambda t: {},
    "T15": lambda t: {},
    "T16": lambda t: _hybrid(t, "Technology", _holders(t, "NVDA")),
}
