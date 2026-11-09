"""Semantic layer: what the SQL generator is told about the database.

Descriptions, units and business definitions (docs/BUSINESS_RULES.md) are written here once.
Categorical values, names, latest dates and coverage are read from the database at start-up, so
the prompt always matches the data. The verified examples were written for this project. None is
an assessment question, and a test enforces that.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portfolioiq.dates import DateCatalog
from portfolioiq.sql_engine import read_schema

TABLE_NOTES = {
    "portfolios": "one row per portfolio",
    "securities": "one row per security (stock or bond)",
    "sectors": "sector reference list",
    "holdings": "current positions, one row per portfolio and security",
    "transactions": "recorded BUY and SELL trades; the history may be incomplete",
    "historical_prices": "daily prices per security",
    "portfolio_performance": "daily performance statistics; units not documented",
    "risk_metrics": "daily risk statistics; units not documented",
    "benchmarks": "benchmark reference list",
}
COLUMN_NOTES = {
    ("portfolios", "total_aum"): "stated assets under management (AUM), USD; not derived from holdings",
    ("portfolios", "benchmark_index"): "equals benchmarks.benchmark_symbol",
    ("securities", "current_price"): "current price, USD per unit",
    ("securities", "market_cap"): "units not documented",
    ("securities", "sector_id"): "NULL for bonds",
    ("securities", "maturity_date"): "bonds only",
    ("securities", "coupon_rate"): "bonds only",
    ("holdings", "current_weight"): "fraction of the portfolio, 0.15 = 15%",
    ("holdings", "purchase_price"): "USD per unit",
    ("holdings", "cost_basis"): "quantity * purchase_price, USD",
    ("transactions", "price"): "USD per unit",
    ("transactions", "fees"): "USD",
}
CATEGORICAL_COLUMNS = (
    ("portfolios", "portfolio_name"), ("portfolios", "status"), ("portfolios", "target_risk_level"),
    ("portfolios", "strategy_type"), ("securities", "asset_type"), ("securities", "exchange"),
    ("securities", "country"), ("securities", "currency"), ("sectors", "sector_name"),
    ("sectors", "industry_group"), ("transactions", "transaction_type"), ("benchmarks", "benchmark_name"),
    ("benchmarks", "benchmark_type"),
)
RELATIONSHIPS = (
    "holdings.portfolio_id = portfolios.portfolio_id",
    "holdings.security_id = securities.security_id",
    "securities.sector_id = sectors.sector_id (bonds have no sector)",
    "transactions.portfolio_id = portfolios.portfolio_id; transactions.security_id = securities.security_id",
    "historical_prices.security_id = securities.security_id",
    "portfolio_performance.portfolio_id = portfolios.portfolio_id",
    "risk_metrics.portfolio_id = portfolios.portfolio_id",
    "portfolios.benchmark_index = benchmarks.benchmark_symbol (implicit link, no foreign key)",
)


@dataclass(frozen=True)
class VerifiedExample:
    id: str
    question: str
    sql: str


VERIFIED_EXAMPLES = (
    VerifiedExample("VE1", "How many securities are there of each asset type?",
                    "SELECT asset_type, COUNT(*) AS security_count FROM securities "
                    "GROUP BY asset_type ORDER BY security_count DESC;"),
    VerifiedExample("VE2", "Which portfolios are benchmarked against the S&P 500 Index?",
                    "SELECT p.portfolio_name FROM portfolios p JOIN benchmarks b "
                    "ON b.benchmark_symbol = p.benchmark_index WHERE b.benchmark_name = 'S&P 500 Index';"),
    VerifiedExample("VE3", "What is the market value of each holding in the Balanced Portfolio?",
                    "SELECT s.symbol, s.company_name, h.quantity * s.current_price AS market_value "
                    "FROM holdings h JOIN securities s ON s.security_id = h.security_id "
                    "JOIN portfolios p ON p.portfolio_id = h.portfolio_id "
                    "WHERE p.portfolio_name = 'Balanced Portfolio' ORDER BY market_value DESC;"),
    VerifiedExample("VE4", "What was the latest Sharpe ratio of each portfolio that has performance data?",
                    "SELECT p.portfolio_name, pp.performance_date, pp.sharpe_ratio FROM portfolio_performance pp "
                    "JOIN portfolios p ON p.portfolio_id = pp.portfolio_id "
                    "WHERE pp.performance_date = (SELECT MAX(performance_date) FROM portfolio_performance) "
                    "ORDER BY pp.sharpe_ratio DESC;"),
    VerifiedExample("VE5", "Total BUY transaction value per portfolio",
                    "SELECT p.portfolio_name, SUM(t.quantity * t.price) AS buy_value FROM transactions t "
                    "JOIN portfolios p ON p.portfolio_id = t.portfolio_id WHERE t.transaction_type = 'BUY' "
                    "GROUP BY p.portfolio_id, p.portfolio_name ORDER BY buy_value DESC;"),
)


# Candidate SQL prompt v2.1: the same examples, except that VE3 no longer teaches a holdings list without holding_id and
# quantity, plus two that show the result-column policy on questions of their own. The baseline context above is unchanged.
CANDIDATE_EXAMPLES = (
    VERIFIED_EXAMPLES[0],
    VERIFIED_EXAMPLES[1],
    VerifiedExample("VE3", "What is the market value of each holding in the Balanced Portfolio?",
                    "SELECT h.holding_id, s.symbol, s.company_name, h.quantity, h.quantity * s.current_price AS market_value "
                    "FROM holdings h JOIN securities s ON s.security_id = h.security_id "
                    "JOIN portfolios p ON p.portfolio_id = h.portfolio_id "
                    "WHERE p.portfolio_name = 'Balanced Portfolio' ORDER BY market_value DESC;"),
    VERIFIED_EXAMPLES[3],
    VERIFIED_EXAMPLES[4],
    VerifiedExample("VE6", "Within each strategy type, how much AUM sits in high-risk funds, and what share of the strategy's AUM is that?",
                    "WITH part AS (SELECT strategy_type, SUM(total_aum) AS high_risk_aum FROM portfolios "
                    "WHERE target_risk_level = 'High' GROUP BY strategy_type), "
                    "whole AS (SELECT strategy_type, SUM(total_aum) AS strategy_aum FROM portfolios GROUP BY strategy_type) "
                    "SELECT part.strategy_type, part.high_risk_aum, whole.strategy_aum, "
                    "ROUND(part.high_risk_aum * 100.0 / whole.strategy_aum, 2) AS high_risk_percentage "
                    "FROM part JOIN whole ON whole.strategy_type = part.strategy_type ORDER BY high_risk_percentage DESC;"),
    VerifiedExample("VE7", "What is the market cap of each Energy stock?",
                    "SELECT s.symbol, s.market_cap FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id "
                    "WHERE sec.sector_name = 'Energy';"),
)


# Candidate v2.2: the v2.1 examples, except that VE4 no longer returns the date it only uses to pick the latest row.
CANDIDATE_22_EXAMPLES = tuple(
    VerifiedExample(e.id, e.question,
                    "SELECT p.portfolio_name, pp.sharpe_ratio FROM portfolio_performance pp "
                    "JOIN portfolios p ON p.portfolio_id = pp.portfolio_id "
                    "WHERE pp.performance_date = (SELECT MAX(performance_date) FROM portfolio_performance) "
                    "ORDER BY pp.sharpe_ratio DESC;") if e.id == "VE4" else e
    for e in CANDIDATE_EXAMPLES)


# Candidate v2.4: a latest value is read at the entity's own latest date, so VE4 must not use the table-wide one.
CANDIDATE_24_EXAMPLES = tuple(
    VerifiedExample(e.id, e.question,
                    "SELECT p.portfolio_name, pp.sharpe_ratio FROM portfolio_performance pp "
                    "JOIN portfolios p ON p.portfolio_id = pp.portfolio_id "
                    "WHERE pp.performance_date = (SELECT MAX(x.performance_date) FROM portfolio_performance x "
                    "WHERE x.portfolio_id = pp.portfolio_id) ORDER BY pp.sharpe_ratio DESC;") if e.id == "VE4" else e
    for e in CANDIDATE_22_EXAMPLES)
LATEST_DATE_RULE = "When a question about time-series data gives no date, use the latest date in the relevant table."
CANDIDATE_24_LATEST_DATE_RULE = ("When a question about time-series data gives no date, use the latest date of the entity asked about, among "
                                 "that entity's own rows. Use the latest date of the whole table only if the question asks for it.")


@dataclass(frozen=True)
class SemanticContext:
    text: str
    schema: dict[str, dict[str, str]]
    dates: DateCatalog
    aum_coverage: tuple[float, float]  # holdings market value / stated AUM, min and max
    covered_portfolios: tuple[int, ...]  # portfolio_ids with performance and risk data

    @property
    def version(self) -> str:
        """Short hash of the prompt context, recorded so every answer is reproducible."""
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]

    @classmethod
    def from_db(cls, db_path: Path, examples: tuple[VerifiedExample, ...] = VERIFIED_EXAMPLES,
                latest_rule: str = LATEST_DATE_RULE) -> SemanticContext:
        schema = read_schema(db_path)
        dates = DateCatalog.from_db(db_path)
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            values = {
                (t, c): [r[0] for r in conn.execute(f"SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL ORDER BY 1")]
                for t, c in CATEGORICAL_COLUMNS
            }
            securities = conn.execute("SELECT symbol, company_name, asset_type FROM securities ORDER BY symbol").fetchall()
            coverage = conn.execute(
                "SELECT MIN(v / a), MAX(v / a) FROM (SELECT p.total_aum AS a, SUM(h.quantity * s.current_price) AS v "
                "FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id "
                "JOIN securities s ON s.security_id = h.security_id GROUP BY p.portfolio_id)"
            ).fetchone()
            covered = tuple(r[0] for r in conn.execute(
                "SELECT DISTINCT portfolio_id FROM portfolio_performance "
                "WHERE portfolio_id IN (SELECT portfolio_id FROM risk_metrics) ORDER BY 1"))
        finally:
            conn.close()
        text = _render(schema, values, securities, dates, coverage, covered, examples, latest_rule)
        return cls(text=text, schema=schema, dates=dates, aum_coverage=tuple(coverage), covered_portfolios=covered)


def _render(schema, values, securities, dates: DateCatalog, coverage, covered, examples, latest_rule) -> str:
    lines = ["DATABASE: SQLite. Tables and columns (name TYPE: note):"]
    for table, columns in schema.items():
        lines.append(f"{table} ({TABLE_NOTES[table]}):")
        for column, declared in columns.items():
            notes = [COLUMN_NOTES[(table, column)]] if (table, column) in COLUMN_NOTES else []
            if (table, column) in values and column != "portfolio_name" and column != "benchmark_name":
                notes.append("values: " + ", ".join(f"'{v}'" for v in values[(table, column)]))
            lines.append(f"  {column} {declared}" + (f": {'; '.join(notes)}" if notes else ""))
    lines += ["", "Relationships:"] + [f"- {r}" for r in RELATIONSHIPS]
    lines += ["", "Exact names (text values are case-sensitive):",
              "- portfolios.portfolio_name: " + ", ".join(f"'{v}'" for v in values[("portfolios", "portfolio_name")]),
              "- benchmarks.benchmark_name: " + ", ".join(f"'{v}'" for v in values[("benchmarks", "benchmark_name")]),
              "- securities (symbol = company name, type): " + "; ".join(f"{s} = {n} ({t})" for s, n, t in securities)]
    low, high = (100 * x for x in coverage)
    lines += ["", "Business definitions:",
              "- current_weight is a fraction of the portfolio (0.15 = 15%).",
              "- Market value of a holding = holdings.quantity * securities.current_price. The value (or market value) "
              "of a portfolio = SUM of the market value of all its holdings, bonds included. This is not AUM.",
              f"- AUM (assets under management) = portfolios.total_aum, a stated figure. Recorded holdings cover only "
              f"{low:.1f}% to {high:.1f}% of stated AUM, so AUM is never derived from holdings.",
              "- Cost basis = holdings.cost_basis. Current price = securities.current_price, not a historical close.",
              "- Stocks: securities.asset_type = 'Stock'. Bonds: asset_type = 'Bond'; bonds have no sector.",
              "- Active portfolios: portfolios.status = 'Active' (the only other value is 'Passive').",
              "- Diversification ratio (this dataset's own definition, not a general formula): the number of distinct "
              "stock sectors divided by the total number of holdings (bonds included), per portfolio.",
              "- Performance and risk values have no documented units: return them as stored, without converting, "
              "annualising or relabelling them."]
    latest = "; ".join(f"{t}.{c} {dates.latest(t, c)}" for (t, c) in dates.ranges)
    lines += ["", "Dates are ISO text 'YYYY-MM-DD'. Latest date per table: " + latest + ".",
              latest_rule,
              f"portfolio_performance and risk_metrics contain only portfolio_id {', '.join(map(str, covered))}.",
              "", "Verified examples:"]
    for example in examples:
        lines += [f"Q: {example.question}", f"SQL: {example.sql}"]
    return "\n".join(lines)
