"""Data integrity detail: which data issues affect results. Warnings are documented anomalies; failures make results wrong."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import backend
import ui
from portfolioiq.verification import CheckStatus


def render_checks() -> None:
    report = backend.integrity()
    counts = {s: report.count(s) for s in CheckStatus}
    ui.stat_line([f"{len(report.checks)} checks", f"{counts[CheckStatus.PASS]} PASS", f"{counts[CheckStatus.WARNING]} WARNING",
                  f"{counts[CheckStatus.FAIL]} FAIL", f"{counts[CheckStatus.NOT_APPLICABLE]} not applicable", f"data as of {report.as_of}"])
    if counts[CheckStatus.FAIL]:
        ui.callout("error", "Failures found", "At least one check failed. Figures that depend on the failing data must not be used.")
    checks = {c.id: c for c in report.checks}
    order = {CheckStatus.FAIL: 0, CheckStatus.WARNING: 1, CheckStatus.NOT_APPLICABLE: 2, CheckStatus.PASS: 3}
    ordered = sorted(report.checks, key=lambda c: (order[c.status], c.id))
    ui.head("Invariant checks", "Recomputed now. Also available as: python -m portfolioiq integrity")
    ui.table(["Status", "ID", "Check", "Scope", "Detail", "Rule"],
             [[ui.chip(c.status.value), c.id, c.name, c.scope, c.detail, c.rule or ""] for c in ordered],
             html_columns={0}, widths=["10%", "5%", "19%", "14%", "44%", "8%"])
    ui.footnote("Every check is read-only; the supplied records are never modified or corrected (rule D6)")
    with st.expander("What each warning means for answers"):
        for c in ordered:
            if c.status is not CheckStatus.PASS:
                st.html(f'<div class="piq-foot" style="color:#14202E"><b>{ui.esc(c.id)} {ui.esc(c.name)}</b>: {ui.esc(c.effect)}</div>')

    left, right = st.columns(2, gap="large")
    with left:
        ui.head("Weight totals by portfolio", "Sum of current_weight against 100%")
        totals = pd.DataFrame([{"Portfolio": t.portfolio_name, "Total weight (%)": t.total_weight * 100, "off": abs(t.residual) > 0.0001}
                               for t in report.weight_totals])
        ui.show(ui.dot_plot_reference(totals, "Portfolio", "Total weight (%)", "off", reference=100.0, value_title="Total current_weight (%)"))
        ui.footnote("Off-target portfolios are labelled; the difference is reported as an unattributed residual (B3)")
    with right:
        ui.head("Source anomalies, preserved not corrected")
        ui.table(["Anomaly", "Detail"], [[checks[i].name, checks[i].detail] for i in ("I18", "I19", "I11") if i in checks])
        ui.footnote("Rules D6 and B11: classifications and prices are used as supplied and flagged")

    gaps = checks.get("I13")
    ui.head("Holdings versus transactions", gaps.detail if gaps else None)
    if gaps and gaps.rows:
        ui.table(["Portfolio", "Security", "Holdings quantity", "Net transactions", "Gap"],
                 [[r["portfolio"], r["symbol"], ui.money(r["holdings_quantity"]), ui.money(r["net_transactions"]), ui.money(r["gap"])] for r in gaps.rows],
                 numeric={2, 3, 4})
        ui.footnote("Reconciliation gaps, not proven errors: the transaction history may be incomplete (D6)")
