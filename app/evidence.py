"""Shared evidence display: run modes, SQL safety results, exposure tables and the Financial Answer Receipt panel.

Everything shown here is a field produced by the backend. A field the backend did not produce is left out, and
no figure is calculated here.
"""

from __future__ import annotations

import json
import logging
import time

import streamlit as st

import backend
import ui
from portfolioiq.exposure import ExposureResult
from portfolioiq.llm import LLMCall
from portfolioiq.receipt import FinancialAnswerReceipt
from portfolioiq.replay import DEMO_LABEL
from portfolioiq.sql_guard import SqlValidation
from portfolioiq.sql_tool import SqlToolResult
from portfolioiq.verification import CheckStatus

DEMO, LIVE = "Demo: recorded responses", "Live Gemini"
SOURCE_TEXT = {"api": "live Gemini request", "cache": "saved response (no request sent)",
               "replay": "recorded response (no request sent)"}
DUPLICATE_WINDOW_S = 1.5
LOG = logging.getLogger("portfolioiq.app")

# The guard's checks in the order sql_guard.validate_sql applies them, with the rejection categories of each.
GUARD_STAGES = (
    ("SQL parsed", {"empty", "too_long", "parse_error"}),
    ("Single statement", {"multiple_statements"}),
    ("Read-only SELECT", {"forbidden_operation", "not_select"}),
    ("Allowed tables", {"forbidden_table", "qualified_name", "unknown_table"}),
    ("Allowed functions", {"forbidden_function"}),
    ("Allowed columns", {"unknown_column"}),
)


# --- run mode and request control ------------------------------------------------------------------

def mode_selector(key: str, noun: str, recorded: int, cost: str) -> str:
    """Demo or live, always labelled. Demo is the default, so opening a page can never spend quota."""
    mode = st.segmented_control("Run mode", [DEMO, LIVE], default=DEMO, key=key) or DEMO
    if mode == DEMO:
        ui.callout("demo", DEMO_LABEL, "Gemini's output is replayed from responses recorded during the live evaluation "
                   f"runs and nothing is sent to the API. Validation, SQL execution, calculation and verification run now. "
                   f"Only recorded questions can be answered ({recorded} available).")
    elif backend.gemini_configured():
        ui.callout("warning", "Live mode", f"Each {noun} sends real requests to {backend.settings().gemini_model} ({cost}) "
                   "and uses the free-tier daily quota. Transient failures can add one retry, and failed requests count too.")
    else:
        ui.callout("error", "Live mode unavailable", "No Gemini API key is configured (GEMINI_API_KEY). Use demo mode, or "
                   "the pages that need no Gemini: Exposure Analysis, Evaluation & Evidence and Data & Methodology.")
    return mode


def is_duplicate(state_key: str, signature: tuple) -> bool:
    """True for a second submit that arrived while the same request was running, which would spend quota twice."""
    last = st.session_state.get(state_key)
    return bool(last and last[0] == signature and time.monotonic() - last[1] < DUPLICATE_WINDOW_S)


def mark_finished(state_key: str, signature: tuple) -> None:
    st.session_state[state_key] = (signature, time.monotonic())


def internal_error(where: str, exc: Exception) -> None:
    """Log the exception type only: messages can carry paths or request text. The page shows a plain message."""
    LOG.error("%s failed: %s", where, type(exc).__name__)


# --- SQL safety ------------------------------------------------------------------------------------

def guard_rows(validation: SqlValidation) -> list[list[str]]:
    """The real guard result, stage by stage. Stages after a rejection are shown as not reached, never as passed."""
    failed = next((i for i, (_, categories) in enumerate(GUARD_STAGES) if validation.category in categories), None)
    rows = []
    for index, (name, _) in enumerate(GUARD_STAGES):
        if validation.ok or (failed is not None and index < failed):
            detail = {"Read-only SELECT": f"Statement type: {validation.statement_type}" if validation.ok else "",
                      "Allowed tables": ", ".join(validation.tables) if validation.ok else ""}.get(name, "")
            rows.append([ui.chip("PASS"), name, detail])
        elif index == failed:
            rows.append([ui.chip("FAIL"), name, validation.reason or validation.category])
        else:
            rows.append([ui.chip("N/A"), name, "Not reached: the guard stopped at the rejection above."])
    return rows


def guard_table(validation: SqlValidation) -> None:
    ui.table(["Result", "Guard check", "Detail"], guard_rows(validation), html_columns={0}, widths=["11%", "24%", "65%"])


def safety_panel(result: SqlToolResult) -> None:
    """SQL safety as reported by the guard and the executor for the query that produced this answer."""
    if result.validation is None:
        ui.empty_state("The SQL guard did not run", "Gemini returned no SQL for this question, so there was nothing to "
                       "validate and nothing was executed.")
        return
    guard_table(result.validation)
    settings, execution = backend.settings(), result.execution
    if not result.validation.ok:
        decision, control = "Rejected by the guard: never executed", "Not applied: nothing was executed."
    elif execution is None:
        decision, control = "Not executed", "Not applied: nothing was executed."
    else:
        decision = {"ok": "Allowed and executed", "timeout": "Allowed, then stopped at the time limit",
                    "error": "Allowed, then refused or failed in SQLite"}[execution.status]
        control = "Read-only URI, PRAGMA query_only and SQLite authorizer applied to this connection."
    row_note = ("; this result was cut at the limit" if execution is not None and execution.truncated else "")
    ui.key_values([("Execution", decision), ("Database controls", control),
                   ("Query time limit", f"{settings.sql_timeout_s:g} seconds"),
                   ("Output row limit", f"{settings.sql_max_rows} rows{row_note}")])


def llm_source(calls: list[LLMCall]) -> str:
    return ", ".join(sorted({SOURCE_TEXT[c.source] for c in calls})) or "no model call"


# --- exposure --------------------------------------------------------------------------------------

def exposure_table(result: ExposureResult) -> None:
    """Sector rows, excluded lines and the residual, with the numerator shown beside both methods."""
    rows = [[s.sector, "Eligible stocks", ui.percent(s.method_a), ui.percent(s.method_a), ui.percent(s.method_b),
             ", ".join(f"{h.symbol} {ui.percent(h.weight)}" for h in s.holdings)] for s in result.sectors]
    rows += [[e.label, "Excluded from sector rows", ui.percent(e.weight), ui.percent(e.weight), "—",
              ", ".join(f"{h.symbol} {ui.percent(h.weight)}" for h in e.holdings)] for e in result.excluded]
    if result.residual_reported:
        rows.append(["Unattributed weight residual", "Not cash; not redistributed", ui.percent(result.residual),
                     ui.percent(result.residual), "—", "Not attributed to any holding"])
    ui.table(["Line", "Classification", "Weight % (numerator)", "Method A %", "Method B %", "Holdings (current_weight %)"], rows,
             numeric={2, 3, 4}, total=["Total", "", "", ui.percent(result.method_a_total),
                                       ui.percent(sum(s.method_b for s in result.sectors)), ""])


def calculation_lines(result: ExposureResult) -> None:
    """Formula, numerator, denominator and a worked figure for the largest sector."""
    top = result.sectors[0]
    terms = " + ".join(ui.percent(h.weight) for h in top.holdings)
    ui.formula(f"Method A: {top.sector} = {terms} = {ui.percent(top.method_a)}% of the whole portfolio")
    ui.formula(f"Method B: {top.sector} = {ui.percent(top.method_a)} / {ui.percent(result.eligible_stock_weight)} = "
               f"{ui.percent(top.method_b)}% of the equity sleeve")
    ui.key_values([("Numerator", "Sum of holdings.current_weight of the sector's eligible stocks"),
                   ("Method A denominator", result.method_a_denominator),
                   ("Method B denominator", result.method_b_denominator)])


# --- the receipt panel -----------------------------------------------------------------------------

def render_receipt_evidence(receipt: FinancialAnswerReceipt) -> None:
    """View Calculation & Evidence: every field the backend produced for this answer, collapsed by default."""
    with st.expander("View Calculation & Evidence"):
        _trust_summary(receipt)
        _execution(receipt)
        _sql_evidence(receipt)
        _calculation(receipt)
        _financial_verification(receipt)
        _checks(receipt)
        _provenance(receipt)
        payload = receipt.model_dump_json(indent=2)
        st.download_button("Download full receipt (JSON)", payload, file_name=f"receipt-{receipt.receipt_id}.json",
                           mime="application/json", key=f"dl_{receipt.receipt_id}")


def _trust_summary(receipt: FinancialAnswerReceipt) -> None:
    """Three plain statements, from existing fields: what produced the figures, what was checked, what was not."""
    ui.section_label("How far to trust this answer")
    financial = receipt.sql.financial_verification if receipt.sql is not None else None
    if receipt.exposures:
        produced = "Deterministic Python on stored holding weights. Gemini wrote no figure."
    elif receipt.sql is not None and receipt.sql.sql:
        produced = "A query drafted by Gemini, validated and run read-only. The answer text was written by code."
    else:
        produced = "No figure was produced."
    checked = ("; ".join(sorted({ui.STAMP_TEXT.get(e.verification.value, e.verification.value) for e in receipt.exposures})) if receipt.exposures
               else financial.summary if financial is not None else f"{len(receipt.checks)} check(s); see below.")
    not_checked = ("Whether the supplied data is itself correct, and whether the question was read as you intended."
                   if receipt.status == "answered" else "Nothing was calculated, so there are no figures to check.")
    ui.key_values([("What produced it", produced), ("What was checked", checked), ("What was not checked", not_checked)])


def _financial_verification(receipt: FinancialAnswerReceipt) -> None:
    financial = receipt.sql.financial_verification if receipt.sql is not None else None
    if financial is None:
        return
    ui.section_label("Independent financial verification")
    ui.stamp(financial.status.value, financial.summary)
    if financial.figures:
        ui.table(["Column", "Recognised as", "Outcome", "Detail"], [[f.column, f.figure, f.outcome.replace("_", " "), f.detail] for f in financial.figures],
                 widths=["18%", "34%", "13%", "35%"])
    ui.footnote("Rebuilt in Python from source records. The generated SQL is not run again")


def _execution(receipt: FinancialAnswerReceipt) -> None:
    ui.section_label("Execution")
    if not receipt.tool_calls:
        ui.empty_state("No tool ran", receipt.answer_basis)
        return
    rows = [[c.step, c.tool.replace("_", " "), c.status.replace("_", " "), _argument(c), llm_source(c.llm_calls),
             ui.ms(c.latency_ms)] for c in receipt.tool_calls]
    ui.table(["Step", "Tool", "Status", "Input", "Model output source", "Latency"], rows, numeric={5})
    if receipt.router is not None and receipt.router.reason:
        ui.footnote(f"Router's stated reason: {receipt.router.reason}")
    resolved = [r for r in receipt.resolutions if r.status == "resolved"]
    if resolved:
        ui.footnote("Portfolios resolved by code: " + "; ".join(f"{r.portfolio_name} (rule {r.rule})" for r in resolved))


def _argument(call) -> str:
    text = call.arguments.get("portfolio") or call.arguments.get("question") or ""
    return ui.short(text, 80)


def _sql_evidence(receipt: FinancialAnswerReceipt) -> None:
    sql = receipt.sql
    if sql is None:
        return
    ui.section_label("Generated SQL and safety")
    if sql.sql:
        st.code(sql.sql, language="sql", wrap_lines=True)
    if sql.attempts:
        ui.footnote(f"Gemini's description: {sql.attempts[-1].explanation}", f"{len(sql.attempts)} attempt(s)")
    ui.footnote("SQL parameters: none. Generated SQL runs as validated text, with no bind parameters."
                if not sql.parameters else "SQL parameters: " + ", ".join(sql.parameters))
    safety_panel(sql)
    if sql.date_interpretations:
        ui.footnote(*[f"Relative date: {d.describe()}" for d in sql.date_interpretations])


def _calculation(receipt: FinancialAnswerReceipt) -> None:
    calculated = [e for e in receipt.exposures if e.status == "ok"]
    if not calculated:
        return
    ui.section_label("Calculation")
    if len(calculated) == 1:
        calculation_lines(calculated[0])
        excluded = "; ".join(f"{e.label} {ui.percent(e.weight)}%" for e in calculated[0].excluded) or "none"
        included = sum(len(s.holdings) for s in calculated[0].sectors)
        ui.key_values([("Included records", f"{included} eligible stock holding(s), listed in the table above"),
                       ("Excluded asset classes", excluded)])
    else:
        ui.table(["Portfolio", "Method B denominator", "Excluded", "Verification"],
                 [[e.portfolio_name, e.method_b_denominator,
                   "; ".join(f"{x.label} {ui.percent(x.weight)}%" for x in e.excluded) or "none",
                   ui.STAMP_TEXT.get(e.verification.value, e.verification.value)] for e in calculated])
        ui.footnote("Numerator: sum of holdings.current_weight of each sector's eligible stocks",
                    f"Method A denominator: {calculated[0].method_a_denominator}")


def _checks(receipt: FinancialAnswerReceipt) -> None:
    ui.section_label("Checks")
    if receipt.checks:
        ui.table(["Status", "Check", "Detail"], [[ui.chip(c.status.value), c.name, c.detail] for c in receipt.checks],
                 html_columns={0}, widths=["11%", "29%", "60%"])
    else:
        ui.empty_state("No checks", "No tool result was produced, so there was nothing to check.")


def _provenance(receipt: FinancialAnswerReceipt) -> None:
    ui.section_label("Provenance")
    model, timings, data = receipt.model, receipt.timings, receipt.data
    rows = [("Dataset fingerprint", data.data_fingerprint), ("Data as of", data.data_anchor_date),
            ("Tables read", ", ".join(data.tables) or "none"), ("Model", model.model or "none"),
            ("Model version reported", model.model_version or "not reported"),
            ("Router version", data.router_version), ("SQL prompt version", data.sql_prompt_version or "none"),
            ("Context version", data.context_version),
            ("Gemini requests", f"{model.api_requests} sent · {model.cache_hits} saved · {model.replayed_responses} replayed"),
            ("Tokens", f"prompt {model.prompt_tokens:,} · output {model.output_tokens:,} · thinking {model.thinking_tokens:,}"),
            ("Latency", " · ".join(f"{name} {ui.ms(value)}" for name, value in timings.stages() if value is not None)),
            ("Answer basis", receipt.answer_basis), ("Receipt", f"{receipt.receipt_id} · {receipt.created_utc}"), ("Receipt fingerprint", receipt.fingerprint()[:16])]
    ui.key_values(rows)


def warning_checks(checks) -> list[str]:
    return [c.detail for c in checks if c.status is CheckStatus.WARNING]


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
