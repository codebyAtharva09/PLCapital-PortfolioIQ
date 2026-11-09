"""Ask PortfolioIQ: a plain-English question answered by the real agent, with the path it took and its evidence.

Demo mode replays recorded Gemini responses and never contacts the API; everything after the model output
(validation, read-only SQL, exposure, verification) still runs now. Live mode is an explicit choice.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import backend
import evidence
import ui
from portfolioiq.receipt import FinancialAnswerReceipt
from portfolioiq.replay import DEMO_LABEL

WORKFLOW = {"sql": "SQL", "exposure": "Exposure", "exposure_for_sql_results": "Hybrid", "clarify": "Clarification",
            "decline": "Refusal"}
WORKFLOWS = ["SQL", "Exposure", "Hybrid", "Clarification", "Refusal"]
TOOL_TEXT = {"router": "Intent router", "sql_query": "SQL Query Tool", "sector_exposure": "Exposure calculator"}
FEATURED = ("A1", "Q5", "Q7", "Q9", "A4", "BOND", "A3")  # lookup, ranking, value comparison, exposure, hybrid, bonds, clarification
LIVE_EXAMPLES = ("How many holdings does the Dividend Aristocrats Fund have?", "Which securities are in the Technology sector?",
                 "What are the sector exposures for the Tech Innovation Fund?",
                 "Which high-risk portfolio has the largest Technology exposure?")  # all answered in recorded live runs
MAX_TABLE_ROWS = 25
# Reviewer-facing text for each failure. Raw provider messages stay in the usage ledger.
ERROR_HELP = {
    "not_recorded": ("demo", "Not recorded", "No recorded Gemini response matches this question, so it cannot be answered "
                     "offline and nothing was invented. Choose a recorded example, or use live mode with a key."),
    "not_configured": ("error", "Gemini is not configured", "No GEMINI_API_KEY is set. Use demo mode, or Exposure Analysis, "
                       "which needs no Gemini."),
    "quota_exhausted": ("warning", "Gemini daily quota used up", "The free tier allows 20 requests a day per project and "
                        "resets at midnight Pacific. Nothing was answered. Use demo mode or Exposure Analysis until then."),
    "rate_limited": ("warning", "Gemini rate limit reached", "The free tier allows 5 requests a minute. Wait a minute and "
                     "ask again."),
    "unavailable": ("warning", "Gemini is temporarily unavailable", "The service reported an outage or high demand. Nothing "
                    "was answered. Try again shortly, or use demo mode."),
    "network": ("warning", "Gemini could not be reached", "The request failed or timed out. Check the network and ask again."),
    "budget_exhausted": ("warning", "Request cap reached", "This session's request cap was reached, so nothing further was sent."),
    "api_error": ("error", "Gemini rejected the request", "Check the API key and the configured model. Details are in the "
                  "usage ledger, reports/api_usage.jsonl."),
    "blocked": ("warning", "Gemini declined this request", "Rephrase the question."),
    "malformed_output": ("error", "Invalid model response", "Gemini's output did not match the required format, so it was "
                         "not used. Ask again."),
    "invalid_sql": ("error", "No valid SQL", "Gemini could not produce SQL that passes the guard. Nothing unsafe was run; the "
                    "attempts are in the evidence panel."),
    "execution_error": ("error", "The query failed", "The validated query failed in the database. The reason is in the evidence panel."),
    "timeout": ("error", "Query time limit reached", "The query exceeded the time limit and was stopped."),
    "incomplete_route": ("error", "Incomplete routing decision", "The router did not say which portfolios to compare, so nothing was run."),
    "no_portfolios_in_result": ("error", "No portfolios found", "The SQL step returned no portfolio names, so no exposure was calculated."),
    "invalid_source_data": ("error", "Invalid source data", "The source weights are invalid, so no figures were produced."),
}
UNEXPECTED_KEY = "ask_unexpected"


def render() -> None:
    ui.page_header("Ask PortfolioIQ", "Ask in plain English. The answer shows which workflow ran, and its evidence opens below it.")
    recorded = backend.recorded_questions()
    mode = evidence.mode_selector("ask_mode", "question", len(recorded), "normally one to three")
    live_blocked = mode == evidence.LIVE and not backend.gemini_configured()

    if "ask_question" not in st.session_state:
        st.session_state["ask_question"] = st.session_state.get("ask_draft", "")
    _examples(mode, recorded)
    with st.form("ask", border=False):
        question = st.text_area("Your question", key="ask_question", height=84,
                                placeholder="Ask about portfolios, holdings, sectors or exposure")
        submitted = st.form_submit_button("Run analysis", type="primary", disabled=live_blocked)
    if submitted:
        st.session_state["ask_draft"] = question
        _run(question, mode)

    if st.session_state.pop(UNEXPECTED_KEY, False):
        ui.callout("error", "Something went wrong", "The question could not be processed and no answer was produced. Check "
                   "that the data folder is intact, then try again.")
    history: list[FinancialAnswerReceipt] = st.session_state.get("ask_history", [])
    if not history:
        ui.empty_state("No analysis yet", "Choose an example or type a question, then press Run analysis.")
        return
    _render_receipt(history[0])
    if len(history) > 1:
        with st.expander(f"Earlier this session ({len(history) - 1})"):
            ui.table(["Question", "Workflow", "Status", "Mode"],
                     [[r.question, WORKFLOW.get(r.route, "None"), r.status.replace("_", " "), "demo replay" if _is_demo(r) else "live"]
                      for r in history[1:]])


def _examples(mode: str, recorded: list[dict]) -> None:
    if mode == evidence.DEMO:
        by_id = {r["id"]: r["question"] for r in recorded}
        featured = [by_id[i] for i in FEATURED if i in by_id]
        others = [r["question"] for r in recorded if r["question"] not in featured]
    else:
        featured, others = list(LIVE_EXAMPLES), []
    if not featured:
        return
    st.pills("Example questions", featured, key=f"ask_pill_{mode}", on_change=_use_example, args=(f"ask_pill_{mode}",))
    if others:
        st.selectbox(f"Other recorded questions ({len(others)})", others, index=None, placeholder="Choose a recorded question",
                     key="ask_other", on_change=_use_example, args=("ask_other",))


def _use_example(key: str) -> None:
    value = st.session_state.get(key)
    if value:
        st.session_state["ask_question"] = value


def _run(question: str, mode: str) -> None:
    text = (question or "").strip()
    signature = (mode, text)
    if evidence.is_duplicate("ask_last", signature):
        return
    try:
        with st.spinner("Running the agent…"):
            agent = backend.demo_agent() if mode == evidence.DEMO else backend.live_agent()
            receipt = agent.ask(text, label="app")
    except Exception as exc:  # noqa: BLE001 - the page shows a plain message; the type goes to the log
        evidence.internal_error("agent", exc)
        st.session_state[UNEXPECTED_KEY] = True
        evidence.mark_finished("ask_last", signature)
        return
    st.session_state["ask_history"] = [receipt, *st.session_state.get("ask_history", [])][:6]
    evidence.mark_finished("ask_last", signature)


def _is_demo(receipt: FinancialAnswerReceipt) -> bool:
    return receipt.model.replayed_responses > 0 or receipt.error_kind == "not_recorded"


def _render_receipt(receipt: FinancialAnswerReceipt) -> None:
    if receipt.model.replayed_responses:
        recorded = sorted({c.cached_at for s in receipt.tool_calls for c in s.llm_calls if c.source == "replay" and c.cached_at})
        ui.callout("demo", DEMO_LABEL, "Gemini output replayed from responses recorded at " + ", ".join(recorded)
                   + ". Nothing was sent to the API; validation, SQL execution, exposure and verification ran now.")
    elif receipt.model.cache_hits and not receipt.model.api_requests:
        ui.callout("demo", "Saved response", "This identical request was answered from a saved Gemini response, so no request was sent.")
    st.html(f'<div class="piq-section">Question</div><div class="piq-foot" style="color:#14202E;font-size:14px">{ui.esc(receipt.question)}</div>')
    ui.section_label("Execution path")
    ui.path(WORKFLOWS, WORKFLOW.get(receipt.route))
    _steps(receipt)
    _outcome(receipt)
    _clarification(receipt)
    _result_tables(receipt)
    for caveat in receipt.caveats:
        ui.callout("warning", "Warning", caveat)
    ui.footnote(f"Data as of {receipt.data.data_anchor_date}", f"Status: {receipt.status.replace('_', ' ')}")
    financial = receipt.sql.financial_verification if receipt.route == "sql" and receipt.sql is not None else None
    if financial is not None and receipt.verification.value != "VERIFICATION_FAILED":
        ui.stamp(financial.status.value, financial.summary)
    else:
        ui.stamp(receipt.verification.value, _stamp_reason(receipt))
    evidence.render_receipt_evidence(receipt)


def _steps(receipt: FinancialAnswerReceipt) -> None:
    if not receipt.tool_calls:
        ui.footnote("No tool ran for this question.")
        return
    items = []
    for call in receipt.tool_calls:
        name = TOOL_TEXT[call.tool]
        target = call.arguments.get("portfolio") if call.tool == "sector_exposure" else None
        suffix = f" ({ui.esc(target)})" if target else ""
        items.append(f"<b>{call.step}. {ui.esc(name)}</b>{suffix} {ui.esc(call.status.replace('_', ' '))}")
    ui.steps(items)


def _outcome(receipt: FinancialAnswerReceipt) -> None:
    if receipt.status == "error":
        kind, label, text = ERROR_HELP.get(receipt.error_kind or "", ("error", "No valid result", "No answer could be "
                                           "produced. Details are in the evidence panel."))
        ui.callout(kind, label, text)
        return
    if receipt.status in ("declined", "rejected"):
        ui.callout("ok", "Nothing was run", "The request was refused before any query. PortfolioIQ opens the database read-only.")
    st.html(f'<div class="piq-answer">{ui.esc(receipt.answer)}</div>')


def _clarification(receipt: FinancialAnswerReceipt) -> None:
    clarification = receipt.clarification
    if clarification is None:
        return
    if clarification.candidates:
        ui.section_label("Choose a portfolio")
        for candidate in clarification.candidates:
            if candidate.kind == "portfolio":
                if st.button(f"{candidate.name} · {candidate.reason}", key=f"cand_{receipt.receipt_id}_{candidate.name}"):
                    st.session_state["portfolio"] = candidate.name
                    ui.go("exposure")
            else:
                ui.footnote(f"{candidate.name} (sector): {candidate.reason}")
        ui.footnote("Choosing a portfolio opens its sector exposure, calculated deterministically (no Gemini call)")
    elif clarification.options:
        ui.table(["Suggested options"], [[o] for o in clarification.options])


def _result_tables(receipt: FinancialAnswerReceipt) -> None:
    if receipt.comparison is not None:
        c = receipt.comparison
        ui.head(f"{c.sector} exposure by portfolio", f"Ranked by {c.ranked_by}")
        ui.table(["Portfolio", "Method A (% of portfolio)", "Method B (% of equity sleeve)", "Exposure status"],
                 [[r.portfolio_name, ui.percent(r.method_a), ui.percent(r.method_b), r.exposure_status.replace("_", " ")]
                  for r in c.rows], numeric={1, 2})
    if len(receipt.exposures) == 1 and receipt.exposures[0].status == "ok":
        result = receipt.exposures[0]
        ui.head(f"Sector exposure: {result.portfolio_name}", "Method A and Method B have different denominators and are not interchangeable")
        evidence.exposure_table(result)
        ui.footnote(f"Method A denominator: {result.method_a_denominator}", f"Method B denominator: {result.method_b_denominator}")
        if st.button("Open in Exposure Analysis", key=f"open_{receipt.receipt_id}"):
            st.session_state["portfolio"] = result.portfolio_name
            ui.go("exposure")
    sql = receipt.sql
    if sql is not None and sql.execution is not None and sql.execution.status == "ok":
        title = "Portfolios selected by the SQL step" if receipt.route == "exposure_for_sql_results" else "Query result"
        cut = ", cut at the row limit" if sql.execution.truncated else ""
        ui.head(title, f"{sql.execution.row_count} row(s){cut}")
        columns, rows = sql.execution.columns, sql.execution.rows
        if len(rows) <= MAX_TABLE_ROWS:
            numeric = {i for i in range(len(columns)) if rows and all(isinstance(r[i], (int, float)) for r in rows)}
            ui.table(columns, [[ui.cell(v) for v in r] for r in rows], numeric=numeric)
        else:
            st.dataframe(pd.DataFrame(rows, columns=columns), hide_index=True, width="stretch")


def _stamp_reason(receipt: FinancialAnswerReceipt) -> str:
    state = receipt.verification.value
    if state == "INDEPENDENTLY_RECONCILED":
        return "Two independent calculations (Python and SQL) agree. This checks the calculation, not the source data."
    if state == "RESULT_VALIDATED":
        return "The result passed every check and its figures come from tool results only. This is not a comparison with a reference answer."
    if state == "VERIFICATION_FAILED":
        return "At least one check failed: do not use these figures."
    return "No data result to verify."
