"""Data & Methodology: the dataset, how figures are defined, and what cannot be claimed."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import backend
import ui
from portfolioiq.verification import CheckStatus
from views.integrity import render_checks


def render() -> None:
    ui.page_header("Data & Methodology", "What the database holds, how each figure is defined, and what PortfolioIQ does not claim.")
    data, method, limits, checks = st.tabs(["Data overview", "Methodology", "Known limitations", "Integrity checks"])
    with data:
        _data()
    with method:
        _method()
    with limits:
        _limits()
    with checks:
        render_checks()


def _coverage(report, universe) -> list[list[str]]:
    checks = {c.id: c for c in report.checks}
    rows = [[r["table"], f"portfolio_id {r['portfolios']}", r["dates"]] for r in checks["I15"].rows] if "I15" in checks else []
    anchors = {r["dataset"]: r for r in checks["I22"].rows} if "I22" in checks else {}
    prices, tx = anchors.get("historical_prices.price_date", {}), anchors.get("transactions.transaction_date", {})
    rows += [["historical_prices", f"{universe['securities']} securities", f"{prices.get('first')} to {prices.get('latest')}"],
             ["transactions", f"{universe['transaction_portfolios']} portfolios", f"{tx.get('first')} to {tx.get('latest')}"]]
    return rows


def _data() -> None:
    u, register, report, tables = backend.universe(), backend.portfolio_register(), backend.integrity(), backend.table_counts()
    warnings, failures = report.count(CheckStatus.WARNING), report.count(CheckStatus.FAIL)
    if failures:
        ui.callout("error", f"{failures} data-integrity failure(s)", "Results that depend on the failing data cannot be trusted. See Integrity checks.")
    else:
        ui.callout("warning", f"{warnings} documented data anomalies, 0 failures", "Figures are usable with the stated caveats. See Known limitations.")
    ui.head("Source and tables", f"Supplied CSV files and schema in data/, loaded unmodified into a read-only SQLite database. Data snapshot date: {report.as_of}")
    ui.table(["Table", "Records", "Contents"], [[t["table"], f"{t['rows']:,}", t["note"]] for t in tables], numeric={1}, widths=["24%", "10%", "66%"])
    ui.footnote(f"Dataset fingerprint: {backend.fingerprint()}", "SHA-256 of the supplied CSV files and schema; check I01 compares them with data/SHA256SUMS")

    ui.head("Data coverage", "Which datasets cover which portfolios and dates")
    ui.table(["Dataset", "Covers", "Dates"], _coverage(report, u))

    ui.head("Portfolio register", "Stated fields and figures computed from recorded holdings")
    ui.table(["Portfolio", "Strategy", "Risk", "Status", "Stated AUM (USD)", "Holdings", "Equity %", "Bond %", "Holdings value % of AUM", "Performance data"],
             [[r["name"], r["strategy"], r["risk"], r["status"], ui.money(r["total_aum"]), r["holdings"], ui.percent(r["equity_weight"]), ui.percent(r["bond_weight"]),
               ui.percent(r["aum_coverage"]), "Yes" if r["has_performance"] else "No"] for r in register], numeric={4, 5, 6, 7, 8})
    coverage = [r["aum_coverage"] for r in register if r["aum_coverage"] is not None]
    ui.footnote("Source: portfolios, holdings, securities, portfolio_performance",
                f"Holdings value = quantity x current_price; it covers {ui.percent(min(coverage))}% to {ui.percent(max(coverage))}% of stated AUM and is never called AUM (D4)",
                "Status Active/Passive is used as supplied (D5)")

    left, right = st.columns(2, gap="large")
    with left:
        ui.head("Stated AUM by portfolio", "USD millions, as stated in portfolios.total_aum")
        aum = pd.DataFrame([{"Portfolio": r["name"], "AUM (USD m)": r["total_aum"] / 1e6} for r in register]).sort_values("AUM (USD m)", ascending=False)
        ui.show(ui.ranked_bars(aum, "Portfolio", "AUM (USD m)", value_title="Stated AUM (USD millions)", fmt=",.1f"))
        ui.footnote("Source: portfolios.total_aum", "Stated figure, never summed from holdings (rule D4)")
    with right:
        ui.head("Asset mix by portfolio", "Share of current_weight: equity, bonds, unattributed residual")
        order = [r["name"] for r in sorted(register, key=lambda r: (-r["equity_weight"], r["name"]))]
        mix = pd.DataFrame([{"Portfolio": r["name"], "Part": part, "Share": share * 100} for r in register
                            for part, share in (("Equity", r["equity_weight"]), ("Bonds", r["bond_weight"]), ("Unattributed residual", max(r["residual"], 0.0)))])
        ui.show(ui.stacked_share_bars(mix, "Portfolio", "Part", "Share", {"Equity": ui.EQUITY, "Bonds": ui.BONDS, "Unattributed residual": ui.RESIDUAL}, order))
        ui.footnote("Source: holdings.current_weight, securities.asset_type", "Residual = 100% minus all weights (B3)")


def _method() -> None:
    ui.head("Exposure methodology", "Implemented in src/portfolioiq/exposure.py. Rules are in docs/BUSINESS_RULES.md")
    ui.table(["Topic", "Rule"], [
        ["Weight basis", "holdings.current_weight, a fraction (0.15 = 15%). Market value is never used for exposure (B1)."],
        ["Method A", "Sum of the sector's stock weights, over the whole portfolio. Denominator: 100% of portfolio weight (B2)."],
        ["Method B", "The same sum, over the total weight of eligible stocks. Sectors total 100% (B2)."],
        ["Bonds", "Excluded from sector rows and shown as an excluded line. Never in the Method B denominator (B2)."],
        ["Stocks without a sector", "Excluded from both methods and from the Method B denominator, shown as an excluded line (E4)."],
        ["Residual", "100% minus all recorded weights, stocks and bonds. Shown separately above a 0.01-point tolerance; never treated as cash or redistributed (B3)."],
        ["Missing weights", "Excluded from every figure with a warning naming the holdings (E1)."],
        ["Invalid weights or duplicates", "No figures are produced and verification fails (E2, E3)."],
        ["Bond-only portfolio", "Reported as no eligible stocks. Method B is not calculated, so nothing divides by zero."],
        ["Reconciliation", "Calculated in Python and again in SQL; marked independently reconciled only when they agree within 1e-9. This detects calculation errors, not errors in the source data."],
    ], widths=["24%", "76%"])

    ui.head("Valuation terms", "These are different quantities and are never interchanged")
    ui.table(["Term", "Definition", "Used for"], [
        ["Holdings market value", "quantity x securities.current_price, summed over recorded holdings", "Questions about value (D2, D4)"],
        ["Portfolio AUM", "portfolios.total_aum, the stated figure", "Questions about AUM. Never summed from holdings (D4)"],
        ["Current-weight allocation", "holdings.current_weight, the stated weight", "Sector exposure (B1)"],
        ["Quantity x current price", "One position's market value; current price is securities.current_price, not the last historical close", "Market value only (B11)"],
    ], widths=["24%", "44%", "32%"])

    ui.head("Snapshot and date handling")
    ui.table(["Topic", "Rule"], [
        ["Reference date", "Relative periods are anchored to the latest date of the relevant dataset, read from the data, never today's date (B9, B10)."],
        ["Last week, month, quarter, year", "The previous completed calendar period, Monday to Sunday for weeks."],
        ["Past 7, 30, 90 days, 12 months", "A rolling period ending on the anchor date."],
        ["Disclosure", "The exact start and end dates are recorded in the receipt and shown as a warning with the answer."],
    ], widths=["24%", "76%"])
    report = backend.integrity()
    checks = {c.id: c for c in report.checks}
    if "I22" in checks:
        ui.table(["Date column", "First", "Latest"], [[r["dataset"], r["first"], r["latest"]] for r in checks["I22"].rows])


def _untested() -> list[str]:
    found = []
    official, sql, agent, bench = (backend.report(n) for n in ("official", "sql", "agent", "benchmark"))
    if official:
        ids = [i["id"] for i in official["items"] if i["agent"]["verdict"] == "NOT EVALUATED"]
        if ids:
            found.append("Official questions not evaluated live: " + ", ".join(ids))
    for name, report_ in (("SQL tool", sql), ("Agent integration", agent)):
        ids = [i["id"] for i in (report_ or {}).get("items", []) if i["verdict"] == "NOT EVALUATED"]
        if ids:
            found.append(f"{name} cases not evaluated live on the official model: " + ", ".join(ids))
    if bench:
        ids = [i["id"] for i in bench["items"] if i["live"]["verdict"] == "NOT EVALUATED"]
        if ids:
            found.append("Reliability scenarios with no live end-to-end result: " + ", ".join(ids))
    if not backend.candidate_runs():
        found.append("No candidate SQL prompt has a recorded live evaluation.")
    if backend.report("independent") is None:
        found.append("The development and validation splits of the independent benchmark (35 of 51 cases): NOT YET EVALUATED.")
    return found


def _limits() -> None:
    report = backend.integrity()
    ui.head("Dataset limitations", "Live from the data checks")
    warnings = [c for c in report.checks if c.status is CheckStatus.WARNING]
    ui.table(["ID", "Limitation", "Effect on answers"], [[c.id, c.detail, c.effect] for c in warnings], widths=["6%", "47%", "47%"])

    ui.head("Historical data")
    ui.cards([
        ("Performance and risk", "Stored for portfolios 1 to 4 only. Other portfolios get a 'no data' answer, never an estimate."),
        ("Units not documented", "Performance and risk values are reported as stored. No units, percentage reading or annualisation are invented (D7)."),
    ])
    ui.head("Geography")
    ui.cards([("No geographic diversification claim", "Every security is marked US and USD in the supplied data, including holdings of portfolios with international names. "
               "PortfolioIQ shows no geographic breakdown because the data does not support one.")])
    ui.head("Exposure assumptions")
    ui.cards([("Stated weights", "Exposure relies on the supplied current_weight, which differs from market-value weights. Reconciliation confirms the arithmetic, not the source data."),
              ("Supplied classifications", "Sectors are used as supplied and flagged where questionable, for example Home Depot as Consumer Staples.")])
    ui.head("Model and API dependencies")
    ui.cards([("Gemini needed for new questions", "Routing and SQL drafting need Gemini. Demo mode answers only questions with a complete recording. Exposure and data checks need no model."),
              ("Free-tier quota", "gemini-3.6-flash allows 5 requests a minute and 20 a day per project. Failed requests count.")])
    ui.head("Evaluation limitations")
    ui.cards([("Small benchmark", "Ten official questions can only show 80%, 90% or 100%. The recorded result is one run per question on one model."),
              ("Strict matching", "SQL results must match the reference columns exactly, so a relaxed-only match counts as a failure."),
              ("Verification is not correctness", "A receipt's verification state records what the system checked. Correctness against a reference is judged only by the evaluators.")])
    ui.head("Untested cases", "Derived from the recorded reports")
    untested = _untested()
    if untested:
        ui.table(["Not yet evaluated"], [[line] for line in untested])
    else:
        ui.footnote("Every recorded set has a live result.")
    ui.head("Application limits")
    ui.cards([("Local, single-user tool", "The server binds to localhost and has no authentication layer. Only the light theme is designed."),
              ("Prompt injection", "It can still produce a wrong read-only query. The guard, read-only database and receipt limit and expose this; they do not prevent a wrong answer.")])
