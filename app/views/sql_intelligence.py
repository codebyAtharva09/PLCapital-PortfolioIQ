"""SQL Intelligence: natural language to SQL, with the guard's real verdict and the returned rows.

This page runs only the production SQL tool. There is deliberately no box for typing SQL: a query reaches the
database only through the tool's guard and read-only executor. The guard test bench calls the same guard on a
fixed list of statements and never executes them.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import backend
import evidence
import ui
from portfolioiq.sql_tool import SqlToolResult

FEATURED = ("Q3", "Q6", "Q5", "U4", "U6", "S1")
LIVE_EXAMPLES = ("Which securities are in the Technology sector?", "List each bond with its coupon rate and maturity date.")
GUARD_EXAMPLES = {  # validated only, never executed
    "A safe query": "SELECT portfolio_name FROM portfolios ORDER BY total_aum DESC LIMIT 3",
    "Write operation": "DELETE FROM transactions WHERE transaction_date < '2023-10-01'",
    "Two statements": "SELECT 1; UPDATE portfolios SET total_aum = 0",
    "System table": "SELECT sql FROM sqlite_master",
    "PRAGMA": "PRAGMA table_info(holdings)",
    "Disallowed function": "SELECT load_extension('x')",
    "Table not in the schema": "SELECT password FROM users",
    "Column not in the schema": "SELECT nonexistent_column FROM holdings",
}
STATUS_TEXT = {"ok": "Answered", "no_rows": "Ran, no rows", "rejected": "Rejected", "unanswerable": "Not answerable from this data",
               "needs_clarification": "Needs clarification", "invalid_sql": "No valid SQL", "execution_error": "Execution failed",
               "timeout": "Time limit reached", "llm_error": "Gemini unavailable", "invalid_input": "Question not accepted"}
LLM_HELP = {"not_recorded": "No recorded Gemini draft matches this question, so nothing was generated and nothing was invented. Choose a recorded example.",
            "not_configured": "No GEMINI_API_KEY is set. Use demo mode.",
            "quota_exhausted": "The free-tier daily quota is used up; it resets at midnight Pacific. Use demo mode until then.",
            "rate_limited": "The free tier allows 5 requests a minute. Wait a minute and ask again.",
            "unavailable": "Gemini reported an outage or high demand. Try again shortly, or use demo mode.",
            "network": "Gemini could not be reached. Check the network and ask again."}


def render() -> None:
    ui.page_header("SQL Intelligence", "A question becomes one read-only SELECT. See the SQL, what the guard decided, and the rows it returned.")
    recorded = backend.recorded_sql_questions()
    mode = evidence.mode_selector("sql_mode", "question", len(recorded), "normally one, two if a repair is needed")
    live_blocked = mode == evidence.LIVE and not backend.gemini_configured()

    ui.head("A. Ask a database question")
    if "sql_question" not in st.session_state:
        st.session_state["sql_question"] = st.session_state.get("sql_draft", "")
    _examples(mode, recorded)
    with st.form("sql", border=False):
        question = st.text_area("Database question", key="sql_question", height=70, placeholder="For example: which portfolios hold bonds?")
        submitted = st.form_submit_button("Generate and run", type="primary", disabled=live_blocked)
    if submitted:
        st.session_state["sql_draft"] = question
        _run(question, mode)

    if st.session_state.pop("sql_unexpected", False):
        ui.callout("error", "Something went wrong", "The question could not be processed. Check that the data folder is intact, then try again.")
    result: SqlToolResult | None = st.session_state.get("sql_result")
    if result is None:
        ui.empty_state("No query yet", "Choose an example or type a question, then press Generate and run.")
    else:
        _render_result(result)
    _guard_bench()


def _examples(mode: str, recorded: list[dict]) -> None:
    if mode == evidence.DEMO:
        by_id = {r["id"]: r["question"] for r in recorded}
        featured = [by_id[i] for i in FEATURED if i in by_id]
        others = [r["question"] for r in recorded if r["question"] not in featured]
    else:
        featured, others = list(LIVE_EXAMPLES), []
    if featured:
        st.pills("Example questions", featured, key=f"sql_pill_{mode}", on_change=_use_example, args=(f"sql_pill_{mode}",))
    if others:
        st.selectbox(f"Other recorded questions ({len(others)})", others, index=None, placeholder="Choose a recorded question",
                     key="sql_other", on_change=_use_example, args=("sql_other",))


def _use_example(key: str) -> None:
    value = st.session_state.get(key)
    if value:
        st.session_state["sql_question"] = value


def _run(question: str, mode: str) -> None:
    text = (question or "").strip()
    signature = (mode, text)
    if evidence.is_duplicate("sql_last", signature):
        return
    try:
        with st.spinner("Generating and validating SQL…"):
            tool = backend.demo_sql_tool() if mode == evidence.DEMO else backend.live_sql_tool()
            result = tool.answer(text, label="app sql")
    except Exception as exc:  # noqa: BLE001 - the page shows a plain message; the type goes to the log
        evidence.internal_error("sql tool", exc)
        st.session_state["sql_unexpected"] = True
        evidence.mark_finished("sql_last", signature)
        return
    st.session_state["sql_result"] = result
    evidence.mark_finished("sql_last", signature)


def _render_result(result: SqlToolResult) -> None:
    if result.replayed:
        ui.callout("demo", "Recorded draft", "Gemini's SQL was replayed from a recorded response. The guard and the query ran now.")
    elif result.cache_hits and not result.llm_calls:
        ui.callout("demo", "Saved response", "This identical request was answered from a saved Gemini response, so no request was sent.")
    ui.metrics([("Status", STATUS_TEXT[result.status], None),
                ("Rows returned", result.execution.row_count if result.execution else "—", None),
                ("SQL execution", ui.ms(result.sql_latency_ms) if result.execution else "—", None),
                ("Gemini", ui.ms(result.llm_latency_ms) if result.llm_calls else "No request sent", None)], small=True)
    if result.status == "llm_error":
        ui.callout("warning", "No SQL was generated", LLM_HELP.get(result.error_kind or "", result.message if result.error_kind is None else
                   "Gemini could not answer this request. Details are in the usage ledger."))
    elif result.status != "ok":
        ui.callout("warning" if result.status != "rejected" else "ok", STATUS_TEXT[result.status], result.message)

    ui.head("B. Generated SQL", result.attempts[-1].explanation if result.attempts else None)
    if result.sql:
        st.code(result.sql, language="sql", wrap_lines=True)
    else:
        ui.empty_state("No SQL", "Gemini returned no query for this question.")

    ui.head("C. Safety validation", "Reported by the production guard and executor for this query")
    evidence.safety_panel(result)

    ui.head("D. Query result")
    _rows(result)
    for caveat in result.caveats:
        ui.callout("warning", "Warning", caveat)

    ui.head("E. Query details")
    _details(result)


def _rows(result: SqlToolResult) -> None:
    execution = result.execution
    if execution is None:
        ui.empty_state("Not executed", "No query reached the database, so there are no rows.")
        return
    if execution.status != "ok":
        ui.callout("error", "Execution failed", execution.error or "The query did not complete.")
        return
    if not execution.rows:
        ui.empty_state("No rows", "The query ran and returned no rows.")
        return
    cut = f" Only the first {execution.row_count} rows are shown: the result reached the row limit." if execution.truncated else ""
    ui.footnote(f"{execution.row_count} row(s)", f"{len(execution.columns)} column(s): {', '.join(execution.columns)}",
                f"{ui.ms(execution.duration_ms)}{cut}")
    st.dataframe(pd.DataFrame(execution.rows, columns=execution.columns), hide_index=True, width="stretch",
                 height=min(38 + 35 * len(execution.rows), 420))


def _details(result: SqlToolResult) -> None:
    calls = [a.llm for a in result.attempts]
    rows: list[tuple[str, object]] = [
        ("Attempts", f"{len(result.attempts)} (one repair is allowed)"),
        ("Model output source", evidence.llm_source(calls)),
        ("Request hash", calls[-1].request_hash[:16] if calls and calls[-1].request_hash else "none"),
        ("Parameters", "None: generated SQL runs as validated text, with no bind parameters" if not result.parameters else ", ".join(result.parameters)),
        ("SQL prompt version", result.prompt_version or "none"),
        ("Context version", result.context_version),
        ("Data as of", result.data_anchor_date),
        ("Model", result.model or "none"),
    ]
    rows += [("Relative date", d.describe()) for d in result.date_interpretations]
    if result.status not in ("ok", "no_rows"):
        rows.append(("Reason", result.message if result.status != "llm_error" else (result.error_kind or "Gemini error")))
    ui.key_values(rows)
    if len(result.attempts) > 1:
        ui.table(["Attempt", "Guard category", "Execution"],
                 [[a.number, a.validation.category if a.validation else "not validated", a.execution.status if a.execution else "not executed"]
                  for a in result.attempts])
    if result.checks:
        ui.table(["Status", "Check", "Detail"], [[ui.chip(c.status.value), c.name, c.detail] for c in result.checks],
                 html_columns={0}, widths=["11%", "29%", "60%"])


def _guard_bench() -> None:
    ui.head("Guard test bench", "Run the production SQL guard on a fixed statement. Statements here are validated only and are never executed.")
    name = st.selectbox("Statement to validate", list(GUARD_EXAMPLES), key="guard_case")
    statement = GUARD_EXAMPLES[name]
    st.code(statement, language="sql", wrap_lines=True)
    validation = backend.validate_only(statement)
    if validation.ok:
        ui.callout("ok", "Accepted by the guard", f"Category: {validation.category}. Validated only; not executed here.")
    else:
        ui.callout("error", "Rejected by the guard", f"Category: {validation.category}. {validation.reason}")
    evidence.guard_table(validation)
    ui.footnote("A second, independent layer exists: SQLite's read-only URI, query_only and authorizer refuse these even if the guard is bypassed (tests R16, R17).")
