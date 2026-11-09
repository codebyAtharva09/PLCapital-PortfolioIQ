"""PortfolioIQ command-line interface, built on the same backend modules as the Streamlit app.

- `ask`: the Portfolio Agent (M4). Gemini routes the question to the SQL tool, the exposure
  calculator or both; the reply is a Financial Answer Receipt.
- `exposure`: the deterministic resolver and exposure engine (M2), with no LLM involved.
- `sql`: the SQL Query Tool (M3). Gemini drafts one query; it is validated and run read-only.
- `integrity`: the Financial Invariant Engine (M5), database-wide data checks with no LLM involved.

`ask --replay` and `sql --replay` run in DEMO / FIXTURE MODE: Gemini's output is replayed from
responses recorded during the live evaluation runs and nothing is sent to the API (see replay.py).

Exit codes: 0 a result was shown; 2 clarification needed, portfolio not found, unanswerable or out
of scope; 3 input or request rejected; 4 Gemini unavailable (or, in demo mode, no recorded response);
5 no valid result (invalid SQL, execution error, timeout or unusable intermediate result).
`integrity` exits 0 when no check fails and 1 when any check fails.
Output is plain ASCII so it renders correctly in any terminal, including when piped.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import load_settings
from portfolioiq.db import create_reference_db
from portfolioiq.exposure import RESIDUAL_LABEL, ExposureResult, HoldingWeight, calculate_exposure
from portfolioiq.formatting import MINUS, NOT_AVAILABLE, display_value, pct
from portfolioiq.invariants import IntegrityReport, open_for_checks, run_invariants
from portfolioiq.receipt import FinancialAnswerReceipt
from portfolioiq.replay import DEMO_LABEL, RecordedLLM
from portfolioiq.resolver import Catalog, Resolution, resolve_portfolio
from portfolioiq.sql_engine import ensure_reference_db
from portfolioiq.sql_tool import SqlTool, SqlToolResult
from portfolioiq.timing import format_ms, format_stages
from portfolioiq.verification import CheckStatus

EXIT_OK, EXIT_CLARIFY, EXIT_REJECTED, EXIT_LLM, EXIT_NO_RESULT = 0, 2, 3, 4, 5
SQL_EXIT_CODES = {"ok": 0, "no_rows": 0, "needs_clarification": 2, "unanswerable": 2, "rejected": 3,
                  "invalid_input": 3, "llm_error": 4, "invalid_sql": 5, "execution_error": 5, "timeout": 5}
ASK_EXIT_CODES = {"answered": 0, "no_data": 0, "needs_clarification": 2, "not_found": 2, "unanswerable": 2,
                  "declined": 2, "rejected": 3, "invalid_input": 3}
NO_RESULT_KINDS = frozenset({"invalid_sql", "execution_error", "timeout", "no_portfolios_in_result",
                             "incomplete_route", "invalid_source_data"})
ROUTE_LABELS = {"sql": "SQL query", "exposure": "sector exposure",
                "exposure_for_sql_results": "SQL query, then sector exposure for each portfolio found",
                "clarify": "clarification", "decline": "declined", "none": "none"}
MAX_ROWS_SHOWN = 20
_ASCII = str.maketrans({"—": "-", "–": "-", "−": "-", "‘": "'", "’": "'",
                        "“": '"', "”": '"', "…": "...", "×": "x", " ": " "})


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "ask":
        return _ask_command(args)
    if args.command == "sql":
        return _sql_command(args)
    if args.command == "integrity":
        return _integrity_command(args)
    settings = load_settings()
    conn = create_reference_db(settings.data_dir, settings.schema_path)
    try:
        return _exposure_command(conn, args)
    finally:
        conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portfolioiq", description="PortfolioIQ: trustworthy portfolio intelligence.")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="ask in plain English; Gemini routes the question to the right tool")
    ask.add_argument("question", help='for example "What are the sector exposures for the Balanced Portfolio?"')
    ask.add_argument("--json", action="store_true", help="print the full Financial Answer Receipt as JSON")
    ask.add_argument("--save", metavar="PATH", help="also write the receipt as JSON to this file")
    ask.add_argument("--replay", action="store_true",
                     help=f"{DEMO_LABEL}: replay recorded Gemini responses offline; nothing is sent to the API")
    exposure = commands.add_parser("exposure", help="sector exposure for one portfolio (deterministic, no LLM)")
    exposure.add_argument("portfolio", help='portfolio name, for example "Balanced Portfolio"')
    exposure.add_argument("--question", help="the original question, used for the grounding and alias checks")
    exposure.add_argument("--json", action="store_true", help="print the resolution and the result as JSON")
    sql = commands.add_parser("sql", help="answer a question with one validated, read-only SQL query (Gemini)")
    sql.add_argument("question", help='for example "How many portfolios do we have?"')
    sql.add_argument("--json", action="store_true", help="print the full result as JSON")
    sql.add_argument("--replay", action="store_true",
                     help=f"{DEMO_LABEL}: replay a recorded Gemini draft offline; nothing is sent to the API")
    integrity = commands.add_parser("integrity", help="database-wide data checks (deterministic, no LLM)")
    integrity.add_argument("--json", action="store_true", help="print every check as JSON")
    return parser


def build_sql_tool(replay: bool = False) -> SqlTool:
    settings = load_settings()
    return SqlTool.from_settings(settings, llm=RecordedLLM(settings) if replay else None)


def build_agent(replay: bool = False) -> PortfolioAgent:
    settings = load_settings()
    return PortfolioAgent.from_settings(settings, llm=RecordedLLM(settings) if replay else None)


def _ask_command(args: argparse.Namespace) -> int:
    receipt = (build_agent(replay=True) if args.replay else build_agent()).ask(args.question)
    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps(receipt.model_dump(mode="json"), indent=2) if args.json else render_receipt(receipt))
    return ask_exit_code(receipt)


def ask_exit_code(receipt: FinancialAnswerReceipt) -> int:
    if receipt.status != "error":
        return ASK_EXIT_CODES[receipt.status]
    return EXIT_NO_RESULT if receipt.error_kind in NO_RESULT_KINDS else EXIT_LLM


def render_receipt(receipt: FinancialAnswerReceipt) -> str:
    """The Financial Answer Receipt as plain text: answer first, then its evidence."""
    route = ROUTE_LABELS[receipt.route]
    if receipt.router is not None and receipt.router.reason:
        route += f" (router: {receipt.router.reason})"
    lines = [demo_banner(receipt)] if is_demo_receipt(receipt) else []
    lines += [f"Question: {receipt.question}", f"Route: {route}",
              f"Status: {receipt.status.replace('_', ' ').upper()}", f"Answer: {receipt.answer}"]
    clarification = receipt.clarification
    if clarification is not None:
        choices = clarification.options or [f"{c.name}{' (sector)' if c.kind == 'sector' else ''}: {c.reason}"
                                            for c in clarification.candidates]
        lines += [f"  {number}. {choice}" for number, choice in enumerate(choices, 1)]
    if receipt.sql is not None and receipt.sql.sql:
        if receipt.route == "exposure_for_sql_results":
            lines.append(f"Portfolio selection (asked of the SQL tool): {receipt.sql.question}")
        if receipt.sql.attempts and receipt.sql.execution is not None:
            lines.append(f"Query (as described by Gemini): {receipt.sql.attempts[-1].explanation}")
        lines += _sql_lines(receipt.sql)
    if receipt.comparison is not None:
        comparison = receipt.comparison
        lines.append(f"{comparison.sector} exposure by portfolio (ranked by {comparison.ranked_by}):")
        rows = [("Portfolio", "Method A (%)", "Method B (%)", "Exposure status")]
        rows += [(r.portfolio_name, _p(r.method_a), "-" if r.method_b is None else _p(r.method_b),
                  r.exposure_status.replace("_", " ")) for r in comparison.rows]
        lines += [f"  {line}" for line in _table(rows, right_aligned={1, 2})]
    elif len(receipt.exposures) == 1 and receipt.exposures[0].status != "invalid_data":
        result = receipt.exposures[0]
        lines += [f"Sector exposure, {result.portfolio_name}",
                  f"  Method A: share of total portfolio.   Denominator: {result.method_a_denominator}",
                  f"  Method B: equity-sleeve composition.  Denominator: {result.method_b_denominator}"]
        lines += [f"  {line}" for line in _exposure_table(result)]
    elif receipt.exposures:
        lines.append("Largest sector per portfolio (Method A):")
        rows = [("Portfolio", "Largest sector", "Method A (%)")]
        for result in receipt.exposures:
            top = result.sectors[0] if result.sectors else None
            rows.append((result.portfolio_name, top.sector if top else result.status.replace("_", " "),
                         _p(top.method_a) if top else "-"))
        lines += [f"  {line}" for line in _table(rows, right_aligned={2})]
    if receipt.checks:
        lines.append("Checks")
        lines += [f"  {c.status.value:<15}{c.name}: {c.detail}" for c in receipt.checks]
    lines.append(f"Verification: {receipt.verification.value}")
    if receipt.sql is not None and receipt.sql.financial_verification is not None:
        lines.append(f"Financial verification: {receipt.sql.financial_verification.status.value}. {_text(receipt.sql.financial_verification.summary)}")
    lines.append(f"Receipt fingerprint: {receipt.fingerprint()}")
    if receipt.caveats:
        lines += ["Caveats"] + [f"  - {c}" for c in receipt.caveats]
    lines.append(f"Answer basis: {receipt.answer_basis}")
    model, data, timings = receipt.model, receipt.data, receipt.timings
    lines += [
        f"Receipt {receipt.receipt_id} ({receipt.created_utc})",
        f"  {model.provider}: model {model.model or 'none'} (served: {model.model_version or 'not reported'}), "
        f"{model.sdk} {model.sdk_version or '-'}, thinking {model.thinking_level or 'model default'}",
        f"  API requests {model.api_requests}, cache hits {model.cache_hits}, replayed {model.replayed_responses}; "
        f"tokens: prompt {model.prompt_tokens}, output {model.output_tokens}, thinking {model.thinking_tokens}",
        f"  Latency: {format_stages(timings.stages(), sep='; ') or 'not measured'}",
        f"  Data {data.data_fingerprint[:16] or '-'}, as of {data.data_anchor_date}; SQL context {data.context_version}, "
        f"router {data.router_version}",
    ]
    return _text("\n".join(lines))


def is_demo_receipt(receipt: FinancialAnswerReceipt) -> bool:
    """True when any Gemini output in the receipt was replayed, or a replay found nothing recorded."""
    return receipt.model.replayed_responses > 0 or receipt.error_kind == "not_recorded"


def demo_banner(receipt: FinancialAnswerReceipt) -> str:
    if receipt.model.replayed_responses == 0:
        return f"[{DEMO_LABEL}] No recorded Gemini response matches this question; nothing was sent to the API."
    recorded = sorted({c.cached_at for step in receipt.tool_calls for c in step.llm_calls
                       if c.source == "replay" and c.cached_at})
    when = f" recorded {', '.join(recorded)}" if recorded else ""
    return (f"[{DEMO_LABEL}] Gemini output replayed from responses{when}; nothing was sent to the API. "
            "Validation, SQL execution, exposure and verification ran now.")


def _sql_command(args: argparse.Namespace) -> int:
    result = (build_sql_tool(replay=True) if args.replay else build_sql_tool()).answer(args.question)
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        banner = (f"[{DEMO_LABEL}] Gemini output replayed from a recorded response; nothing was sent to the API.\n"
                  if args.replay else "")
        print(banner + render_sql_result(result))
    return SQL_EXIT_CODES[result.status]


def _integrity_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = open_for_checks(ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir))
    try:
        report = run_invariants(conn, settings.data_dir)
    finally:
        conn.close()
    print(json.dumps(report.model_dump(mode="json"), indent=2) if args.json else render_integrity(report))
    return 1 if report.has_failures else 0


def render_integrity(report: IntegrityReport) -> str:
    counts = " / ".join(f"{report.count(s)} {s.value.replace('_', ' ')}" for s in CheckStatus)
    lines = [f"Financial Invariant Engine: {len(report.checks)} checks on the supplied data (as of {report.as_of})",
             f"  {counts}", ""]
    rows = [("ID", "Status", "Check", "Scope")]
    rows += [(c.id, c.status.value.replace("_", " "), c.name, c.scope) for c in report.checks]
    lines += [f"  {line}" for line in _table(rows, right_aligned=set())]
    lines += ["", "Details"]
    for c in report.checks:
        if c.status is not CheckStatus.PASS:
            lines += [f"  {c.id} {c.status.value}: {c.detail}", f"      Effect: {c.effect}"]
    lines.append("Warnings are documented anomalies: results stay usable and answers carry a caveat. "
                 "A failure means results built on that data would be wrong.")
    return _text("\n".join(lines))


def render_sql_result(result: SqlToolResult) -> str:
    execution = result.execution
    status = result.status.replace("_", " ").upper()
    if execution is not None and execution.status == "ok":
        status += f", {execution.row_count} row(s)" + (" (truncated)" if execution.truncated else "")
    lines = [f"Status: {status}", f"Answer: {_text(result.message)}"]
    lines += _sql_lines(result)
    if result.checks:
        lines.append("Checks")
        lines += [f"  {c.status.value:<15}{c.name}: {_text(c.detail)}" for c in result.checks]
    lines.append(f"Verification: {result.verification.value}")
    if result.financial_verification is not None:
        lines.append(f"Financial verification: {result.financial_verification.status.value}. {_text(result.financial_verification.summary)}")
    if result.caveats:
        lines += ["Caveats"] + [f"  - {_text(c)}" for c in result.caveats]
    if result.llm_calls:
        lines.append(f"Model {result.model} (served: {result.model_version or 'not reported'}), google-genai "
                     f"{result.sdk_version}; Gemini request {format_ms(result.llm_latency_ms)} over {result.llm_calls} call(s); "
                     f"SQL {format_ms(result.sql_latency_ms)}; total application {format_ms(result.total_latency_ms)}; tokens: prompt {result.prompt_tokens}, output "
                     f"{result.output_tokens}, thinking {result.thinking_tokens}")
    return "\n".join(lines)


def _sql_lines(result: SqlToolResult) -> list[str]:
    """The final SQL and its result table (first rows only)."""
    lines = []
    execution = result.execution
    if result.sql:
        lines += ["SQL:"] + [f"  {line}" for line in _text(result.sql).splitlines()]
    if execution is not None and execution.status == "ok" and execution.columns:
        shown = execution.rows[:MAX_ROWS_SHOWN]
        label = f"Result (first {len(shown)} of {execution.row_count} rows):" if execution.row_count > len(shown) else "Result:"
        rows = [tuple(execution.columns)] + [tuple(_cell(v) for v in row) for row in shown]
        numeric = {i for i in range(len(execution.columns)) if all(isinstance(r[i], (int, float)) for r in shown if r[i] is not None)}
        lines += [label] + [f"  {line}" for line in _table(rows, right_aligned=numeric)]
    return lines


def _text(value: str) -> str:
    return value.translate(_ASCII).encode("ascii", "replace").decode("ascii")


def _cell(value) -> str:
    return _text(display_value(value))


def _exposure_command(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    resolution = resolve_portfolio(Catalog.from_db(conn), args.portfolio, question=args.question)
    result = calculate_exposure(conn, resolution.portfolio_id) if resolution.status == "resolved" else None
    if args.json:
        payload = {"resolution": resolution.model_dump(mode="json"),
                   "exposure": result.model_dump(mode="json") if result else None}
        print(json.dumps(payload, indent=2))
    else:
        print(render_resolution(resolution))
        if result is not None:
            print()
            print(render_exposure(result))
    if result is not None:
        return EXIT_OK
    return EXIT_REJECTED if resolution.status in ("rejected", "invalid_input") else EXIT_CLARIFY


def render_resolution(resolution: Resolution) -> str:
    if resolution.status == "resolved":
        lines = [f"Portfolio: {resolution.portfolio_name}  "
                 f"(rule {resolution.rule}, matched '{resolution.matched_phrase}')",
                 f"  {resolution.message}"]
        if resolution.sector_terms:
            lines.append(f"  Sector terms in the question: {', '.join(resolution.sector_terms)}")
        return "\n".join(lines)
    label = resolution.status.replace("_", " ").capitalize()
    lines = [f"{label} (rule {resolution.rule}): {resolution.message}"]
    for number, candidate in enumerate(resolution.candidates, 1):
        kind = " (sector)" if candidate.kind == "sector" else ""
        lines.append(f"  {number}. {candidate.name}{kind}: {candidate.reason}")
    return "\n".join(lines)


def render_exposure(result: ExposureResult) -> str:
    lines = [f"Sector exposure, {result.portfolio_name}", result.message, ""]
    if result.status != "invalid_data":
        lines += [
            f"Method A: share of total portfolio.   Denominator: {result.method_a_denominator}",
            f"Method B: equity-sleeve composition.  Denominator: {result.method_b_denominator}",
            "",
        ]
        lines += _exposure_table(result) + [""]
    lines.append("Checks")
    lines += [f"  {c.status.value:<15}{c.name}: {c.detail}" for c in result.checks]
    lines.append(f"Verification: {result.verification.value}. {result.reconciliation.detail}")
    if result.caveats:
        lines += ["", "Caveats"] + [f"  - {c}" for c in result.caveats]
    if result.status != "invalid_data":
        lines += ["", "Values are rounded half up to 2 decimals for display; totals use unrounded values."]
    return "\n".join(lines)


def _exposure_table(result: ExposureResult) -> list[str]:
    """Method A and B by sector, excluded lines, the residual and totals."""
    rows = [("Sector", "Method A (%)", "Method B (%)", "Holdings (current_weight %)")]
    rows += [(s.sector, _p(s.method_a), _p(s.method_b), _holdings(s.holdings)) for s in result.sectors]
    rows += [(e.label, _p(e.weight), "-", _holdings(e.holdings)) for e in result.excluded]
    if result.residual_reported:
        rows.append((RESIDUAL_LABEL, _p(result.residual), "-", "not cash; not redistributed (rule B3)"))
    method_b_total = _p(math.fsum(s.method_b for s in result.sectors)) if result.sectors else "-"
    rows.append(("Total", _p(result.method_a_total), method_b_total, ""))
    return _table(rows, right_aligned={1, 2})


def _p(fraction: float) -> str:
    return pct(fraction).replace(MINUS, "-").replace(NOT_AVAILABLE, "-")


def _holdings(holdings: list[HoldingWeight]) -> str:
    return ", ".join(f"{h.symbol} {_p(h.weight)}" for h in holdings if h.weight is not None)


def _table(rows: list[tuple[str, ...]], right_aligned: set[int]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    out = []
    for row in rows:
        cells = [cell.rjust(widths[i]) if i in right_aligned else cell.ljust(widths[i]) for i, cell in enumerate(row)]
        out.append("  ".join(cells).rstrip())
    return out
