"""Exposure Analysis: the deterministic calculator, warnings first, both methods with their denominators."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import backend
import evidence
import ui
from portfolioiq.exposure import BONDS_LABEL, RESIDUAL_LABEL, UNCLASSIFIED_LABEL
from portfolioiq.verification import CheckStatus
from views.explorer import portfolio_selector, render_holdings

INPUT_CHECKS = ("Weights present", "Weights valid (0 to 1)", "No duplicate holdings")


def render() -> None:
    ui.page_header("Exposure Analysis", "Sector exposure from stated weights, calculated by the exposure engine with no Gemini call. "
                   "Two methods with different denominators, computed twice and reconciled.")
    name = portfolio_selector("exposure_portfolio")
    p = backend.portfolio_by_name(name)
    result = backend.exposure(p["portfolio_id"])
    tab_exposure, tab_reconcile, tab_holdings = st.tabs(["Sector exposure", "Reconciliation", "Holdings and value basis"])
    with tab_exposure:
        _exposure(result)
    with tab_reconcile:
        _reconciliation(p["portfolio_id"])
    with tab_holdings:
        render_holdings(name)


def _reconciliation(portfolio_id: int) -> None:
    """Each measurement of the portfolio with its own basis. They answer different questions and are never merged."""
    r = backend.reconciliation(portfolio_id)
    ui.head("Four measurements, four bases", "Rebuilt in Python from source records")
    for warning in r.warnings:
        ui.callout("warning", "Read this first", warning)
    ui.table(["Measurement", "Value", "Basis"], [
        ["Stated AUM (USD)", ui.money(r.stated_aum) if r.stated_aum is not None else "not recorded", "portfolios.total_aum, a stated figure"],
        ["Holdings market value (USD)", ui.money(r.market_value_total), "Sum of quantity x current price over every holding, bonds included"],
        ["Holdings value as % of stated AUM", f"{ui.percent(r.holdings_value_share_of_aum)}%" if r.holdings_value_share_of_aum is not None else "n/a", "Recorded holdings cover only this part of AUM"],
        ["Recorded weight", f"{ui.percent(r.weight_recorded)}%", "Sum of holdings.current_weight"],
        ["Unattributed residual", f"{ui.percent(r.residual)}%", "100% minus recorded weight; not cash; not redistributed"],
    ], numeric={1}, widths=["32%", "18%", "50%"])
    ui.head("Where the weight sits")
    ui.table(["Group", "Stated weight %", "Note"], [
        ["Stocks with a sector", ui.percent(r.weight_stocks_classified), "Method B denominator"],
        ["Stocks without a sector", ui.percent(r.weight_stocks_unclassified), "Reported separately; in no sector row"],
        ["Bonds", ui.percent(r.weight_bonds), "Excluded from sector rows; part of the Method A denominator"],
        ["Holdings with no weight", r.missing_weights, "Count of holdings, not a percentage"],
    ], numeric={1}, widths=["32%", "18%", "50%"])
    if r.sectors:
        ui.head("Stated weight beside market-value share", "These differ because stated weights and current prices are separate inputs")
        ui.table(["Sector", "Stated weight %", "Market value (USD)", "Market-value share of portfolio %"],
                 [[s["sector"], ui.percent(s["weight"]), ui.money(s["market_value"]), ui.percent(s["market_value_share"])] for s in r.sectors], numeric={1, 2, 3})
    ui.footnote(f"Market value: stocks {ui.money(r.market_value_stocks)}, bonds {ui.money(r.market_value_bonds)}")


def _weight(result, label: str) -> float:
    return sum(e.weight for e in result.excluded if e.label == label)


def _exposure(result) -> None:
    if result.status == "invalid_data":
        ui.callout("error", "Exposure not calculated", f"{result.message} Fix the source data; nothing is estimated.")
        ui.stamp(result.verification.value, "Invalid source weights: no figures are produced (rule E2).")
        _input_checks(result)
        return

    bonds, unclassified = _weight(result, BONDS_LABEL), _weight(result, UNCLASSIFIED_LABEL)
    _warnings(result, bonds, unclassified)
    ui.metrics([("Eligible stock weight", f"{ui.percent(result.eligible_stock_weight)}%", "Method B denominator"),
                ("Bonds (excluded)", f"{ui.percent(bonds)}%", "Outside sector rows"),
                ("Stocks without a sector", f"{ui.percent(unclassified)}%", "Outside both methods"),
                ("Unattributed residual", f"{ui.percent(result.residual)}%", "Not cash"),
                ("Recorded weight", f"{ui.percent(result.total_weight)}%", "All holdings")])
    parts = [("Eligible equities", result.eligible_stock_weight, ui.EQUITY), ("Bonds (excluded)", bonds, ui.BONDS)]
    if unclassified:
        parts.append(("Stocks without a sector (excluded)", unclassified, ui.UNCLASSIFIED))
    parts.append(("Unattributed residual", max(result.residual, 0.0), ui.RESIDUAL))
    ui.segmented_bar(parts)
    ui.stamp(result.verification.value, result.reconciliation.detail)

    if result.status == "no_eligible_stocks":
        ui.empty_state("No eligible stocks", f"{result.message} Sector exposure is not applicable, so nothing is calculated "
                       "or invented (rule B2). Method B has no denominator here, so no division is attempted.")
        _exclusions(result, bonds, unclassified)
        _checks(result)
        return

    ui.head("Results", "The figures below are the exposure engine's output; rounding is applied at display only (E6)")
    evidence.exposure_table(result)
    ui.footnote("Totals use unrounded values and may show 100.01%", f"Source: holdings.current_weight · As of {backend.as_of()}")

    ui.head("How each method is calculated", "These answer different questions and are not interchangeable")
    left, right = st.columns(2, gap="medium")
    top = result.sectors[0]
    with left:
        ui.cards([("Method A: portfolio-wide exposure", "Sector stock weight as a share of the whole portfolio. Bonds and any residual stay in the denominator.")])
        ui.key_values([("Numerator", f"{top.sector}: sum of its stocks' current_weight = {ui.percent(top.method_a)}%"),
                       ("Denominator", result.method_a_denominator), ("Result", f"{ui.percent(top.method_a)}% of the portfolio")])
    with right:
        ui.cards([("Method B: equity-sleeve exposure", "Sector stock weight divided by eligible stock weight, so the named sectors total 100%.")])
        ui.key_values([("Numerator", f"{top.sector}: sum of its stocks' current_weight = {ui.percent(top.method_a)}%"),
                       ("Denominator", result.method_b_denominator), ("Result", f"{ui.percent(top.method_b)}% of the equity sleeve")])
    if abs(result.eligible_stock_weight - 1.0) < 1e-9:
        ui.footnote("Both methods show the same figures for this portfolio only because eligible stocks are 100% of its weight.")

    ui.head("Sector exposure, by method", "Same figures as the results table. Bars are shares of different totals: see the legend")
    chart = pd.DataFrame([{"Sector": s.sector, "Method": m, "Share": v * 100} for s in result.sectors
                          for m, v in (("Method A (% of portfolio)", s.method_a), ("Method B (% of equity sleeve)", s.method_b))])
    ui.show(ui.grouped_method_bars(chart, "Sector", value_title="Share (%)"))

    _exclusions(result, bonds, unclassified)
    _checks(result)


def _warnings(result, bonds: float, unclassified: float) -> None:
    """Every warning for this portfolio, before any figure or chart."""
    if bonds > 0:
        ui.callout("warning", "Bonds excluded", f"Bonds make up {ui.percent(bonds)}% of this portfolio and are excluded from "
                   "sector rows. They are shown in the exclusion summary.")
    for caveat in result.caveats:
        ui.callout("warning", "Warning", caveat)
    if not bonds and not result.caveats:
        ui.footnote("No warnings for this portfolio.")


def _exclusions(result, bonds: float, unclassified: float) -> None:
    ui.head("What is outside the sector figures")
    rows = [[BONDS_LABEL, f"{ui.percent(bonds)}%", "Not given a sector. Stays in the Method A denominator; never in the Method B denominator."],
            [UNCLASSIFIED_LABEL, f"{ui.percent(unclassified)}%", "Not allocated to a named sector. Excluded from both methods and from the Method B denominator."],
            [RESIDUAL_LABEL, f"{ui.percent(result.residual)}%", "100% minus all recorded weights. Shown separately; not labelled cash and not redistributed."
             + ("" if result.residual_reported else " Within tolerance for this portfolio.")]]
    ui.table(["Line", "% of portfolio", "Treatment"], rows, numeric={1}, widths=["32%", "14%", "54%"])


def _input_checks(result) -> None:
    ui.head("Input validation")
    rows = [[ui.chip(c.status.value), c.name, c.detail] for c in result.checks if c.name in INPUT_CHECKS]
    ui.table(["Status", "Check", "Detail"], rows, html_columns={0}, widths=["11%", "29%", "60%"])


def _checks(result) -> None:
    flagged = sum(1 for c in result.checks if c.status in (CheckStatus.WARNING, CheckStatus.FAIL))
    with st.expander(f"Checks ({len(result.checks)}, {flagged} flagged)"):
        ui.table(["Status", "Check", "Detail", "Rule"], [[ui.chip(c.status.value), c.name, c.detail, c.rule or ""] for c in result.checks],
                 html_columns={0}, widths=["11%", "26%", "53%", "10%"])
