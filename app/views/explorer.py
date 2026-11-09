"""Holdings detail: what a portfolio holds, with stated weights and market-value weights kept apart."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import backend
import ui


def portfolio_selector(key: str) -> str:
    names = backend.portfolio_names()
    current = st.session_state.get("portfolio", names[0])
    index = names.index(current) if current in names else 0
    choice = st.selectbox("Portfolio", names, index=index, key=key)
    st.session_state["portfolio"] = choice
    return choice


def render_holdings(name: str) -> None:
    p = backend.portfolio_by_name(name)
    ui.key_values([("Strategy", p["strategy"]), ("Risk level", p["risk"]),
                   ("Benchmark", f"{p['benchmark_name']} ({p['benchmark_index']})" if p["benchmark_name"] else p["benchmark_index"]),
                   ("Status", p["status"]), ("Stated AUM (USD)", ui.money(p["total_aum"])), ("Created", p["created"]),
                   ("Holdings value as % of stated AUM", f"{ui.percent(p['aum_coverage'])}% (rule D4: holdings value is not AUM)"),
                   ("Performance and risk data", "Available" if p["has_performance"] else "None recorded for this portfolio")])
    rows = backend.holdings(p["portfolio_id"])
    view = st.segmented_control("Show", ["All", "Stocks", "Bonds"], default="All", key="holdings_filter")
    shown = [r for r in rows if view in (None, "All") or r["asset_type"] == view[:-1]]
    ui.head(f"Holdings ({len(shown)} of {len(rows)})", name)
    if not shown:
        ui.empty_state("No holdings of this type", f"{name} holds no {(view or 'such').lower()}.")
    else:
        frame = pd.DataFrame([{
            "Symbol": r["symbol"], "Name": r["name"], "Type": r["asset_type"], "Sector": r["sector"] or "—",
            "Weight %": r["weight"] * 100 if r["weight"] is not None else None,
            "Quantity": r["quantity"], "Current price": r["current_price"], "Market value (USD)": r["market_value"],
            "Value weight %": r["market_value_weight"] * 100, "Cost basis (USD)": r["cost_basis"],
        } for r in shown])
        st.dataframe(frame, hide_index=True, width="stretch", column_config={
            "Weight %": st.column_config.NumberColumn(format="%.2f", help="holdings.current_weight (stated)"),
            "Quantity": st.column_config.NumberColumn(format="localized"),
            "Current price": st.column_config.NumberColumn(format="%.2f", help="securities.current_price (B11)"),
            "Market value (USD)": st.column_config.NumberColumn(format="localized", help="quantity x current_price"),
            "Value weight %": st.column_config.NumberColumn(format="%.2f", help="share of market value"),
            "Cost basis (USD)": st.column_config.NumberColumn(format="localized"),
        })
        ui.footnote("Source: holdings, securities, sectors", "Weight % is the stated current_weight; Value weight % is the share of market value")

    ui.head("Stated weight vs market-value weight", "Exposure always uses the stated weight (rule B1)")
    compare = pd.DataFrame([{"Holding": r["symbol"], "Stated weight %": r["weight"] * 100,
                             "Market-value weight %": r["market_value_weight"] * 100}
                            for r in rows if r["weight"] is not None])
    if compare.empty:
        ui.empty_state("No weights", "This portfolio has no recorded weights to compare.")
        return
    compare = compare.sort_values("Stated weight %", ascending=False)
    ui.show(ui.dumbbell(compare, "Holding", "Stated weight %", "Market-value weight %", start_name="Stated (current_weight)",
                        end_name="Market value", value_title="Share of portfolio (%)"))
    with st.expander("View as table"):
        ui.table(["Holding", "Stated weight %", "Market-value weight %"],
                 [[r["Holding"], f"{r['Stated weight %']:.2f}", f"{r['Market-value weight %']:.2f}"]
                  for r in compare.to_dict("records")], numeric={1, 2})
    ui.footnote("The two bases disagree in the supplied data, so market-value answers can differ from exposure figures for the same portfolio (I20)")
