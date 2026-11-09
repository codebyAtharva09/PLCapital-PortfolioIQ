"""Overview: what PortfolioIQ is, how it answers, and how to test it in five minutes."""

from __future__ import annotations

import streamlit as st

import backend
import ui
from portfolioiq.verification import CheckStatus

DESCRIPTION = ("An AI-powered portfolio analytics system that translates natural-language questions into controlled SQL "
               "queries and deterministic financial calculations, with evidence-backed results.")
EXAMPLE_ASK = "Which high-risk portfolio has the largest Technology exposure?"  # A4: a recorded live run, hybrid route
EXAMPLE_SQL = "Which securities are in the Technology sector?"  # Q3: a recorded live run
EXAMPLE_PORTFOLIO = "Balanced Portfolio"


def render() -> None:
    ui.page_header("PortfolioIQ", DESCRIPTION, subtitle="AI-Powered Portfolio Analytics Agent")
    _evidence_strip()
    ui.head("What it does")
    ui.cards([
        ("Natural Language to SQL", "Converts portfolio questions into validated, read-only SQL queries."),
        ("Deterministic Exposure Analysis", "Calculates portfolio sector exposures through Python-based financial logic with "
         "explicit methodology and denominator reporting."),
        ("Evidence-Based Portfolio Intelligence", "Combines query results, calculations, validation and traceability into inspectable answers."),
    ])
    ui.table(["Assessed capability", "Where to test it", "Needs Gemini?"], [
        ["SQL Query Tool", "SQL Intelligence", "Demo replay needs none; new questions do"],
        ["Exposure Calculator", "Exposure Analysis", "No"],
        ["CLI Portfolio Agent", "CLI & Setup (commands and real output)", "Offline replay needs none; new questions do"],
        ["Evaluator", "Evaluation & Evidence", "Re-scoring needs none; a new live run does"],
    ], widths=["26%", "36%", "38%"])
    _quick_test()
    ui.head("How an answer is produced", "The path in the source code (src/portfolioiq/agent.py). Gemini chooses the tool and drafts SQL; code computes every figure and writes every answer.")
    ui.flow([
        ("User question", "Input validated by code"),
        ("Intent router", "One Gemini request returns a validated decision"),
        ("Selected tool", "SQL, exposure, both, clarify or decline"),
        ("Execution", "Read-only SQL; exposure engine; or SQL then exposure"),
        ("Validation", "SQL guard, result checks, Python and SQL reconciliation"),
        ("Answer and receipt", "Composed by code from tool results"),
    ])
    left, right = st.columns(2, gap="large")
    with left:
        _dataset()
    with right:
        _stack()


def _evidence_strip() -> None:
    official = backend.report("official")
    report = backend.integrity()
    items = []
    if official:
        agent = official["summary"]["agent"]
        failed = ", ".join(i["id"] for i in official["items"] if i["agent"]["verdict"] == "FAIL")
        original = (backend.report("baseline_official") or {}).get("summary", {}).get("agent", {})
        history = f"; original baseline {original['PASS']} of {original['total']}" if original else ""
        note = (f"FAIL: {failed}" if failed else "No failures") + history
        items.append(("Official benchmark", f"{agent['PASS']} of {agent['total']}", note))
    items.append(("Data integrity checks", f"{report.count(CheckStatus.FAIL)} failed", f"{report.count(CheckStatus.WARNING)} documented warnings"))
    u = backend.universe()
    items.append(("Portfolios", u["portfolios"], f"data as of {report.as_of}"))
    items.append(("Gemini", "Key set" if backend.gemini_configured() else "Not required", "Demo mode replays recorded responses"))
    ui.metrics(items)


def _quick_test() -> None:
    ui.head("Try PortfolioIQ in 5 minutes", "Each step opens the real page with a genuine example. Nothing here sends a Gemini request.")
    steps = [
        ("1. Ask a portfolio question", "Runs the agent and shows the workflow it chose.", "Open Ask PortfolioIQ", "step_ask", _open_ask),
        ("2. Inspect generated SQL", "See the query, the guard's verdict and the rows.", "Open SQL Intelligence", "step_sql", _open_sql),
        ("3. Verify sector exposure", "Both methods, their denominators and what is excluded.", "Open Exposure Analysis", "step_exposure", _open_exposure),
        ("4. Inspect the evaluator", "The benchmark, failures included, and how to re-score it.", "Open Evaluation & Evidence", "step_eval", _open_eval),
    ]
    for column, (title, body, label, key, action) in zip(st.columns(4, gap="small"), steps):
        with column, st.container(border=True, height="stretch"):
            st.html(f'<div class="piq-card" style="border:none;padding:0;background:transparent"><div class="h">{ui.esc(title)}</div><div class="b">{ui.esc(body)}</div></div>')
            if st.button(label, key=key, width="stretch"):
                action()


def _open_ask() -> None:
    st.session_state["ask_draft"] = EXAMPLE_ASK
    st.session_state.pop("ask_question", None)
    ui.go("ask")


def _open_sql() -> None:
    st.session_state["sql_draft"] = EXAMPLE_SQL
    st.session_state.pop("sql_question", None)
    ui.go("sql")


def _open_exposure() -> None:
    st.session_state["portfolio"] = EXAMPLE_PORTFOLIO
    ui.go("exposure")


def _open_eval() -> None:
    ui.go("evaluation")


def _dataset() -> None:
    u, tables = backend.universe(), backend.table_counts()
    ui.head("Dataset snapshot", f"Read from the database. Data snapshot date: {backend.as_of()}")
    ui.metrics([("Tables", len(tables), None), ("Records", f"{sum(t['rows'] for t in tables):,}", None),
                ("Portfolios", u["portfolios"], None), ("Securities", u["securities"], f"{u['stocks']} stocks, {u['bonds']} bonds"),
                ("Holdings", u["holdings"], None)])
    ui.footnote("Fixed historical dataset: not live market data", "Details on Data & Methodology")


def _stack() -> None:
    ui.head("Technology stack", "Pinned in requirements.txt. Each is used only where stated")
    ui.table(["Technology", "Version", "Used for"], [[r["name"], r["version"], r["role"]] for r in backend.tech_stack()], widths=["26%", "14%", "60%"])
