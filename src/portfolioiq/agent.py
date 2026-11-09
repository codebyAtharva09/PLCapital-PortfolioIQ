"""Portfolio Agent: Gemini routes each question; deterministic tools answer it.

    question -> input validation -> router (one Gemini request returning a validated RouterDecision)
      sql                      -> SQL Query Tool, given the user's own question
      exposure                 -> portfolio resolver (B4, B5, SR1) -> exposure engine (B1 to B3)
      exposure_for_sql_results -> SQL Query Tool lists portfolios -> resolver -> exposure engine for each
      clarify, decline         -> a reply without running any tool
    -> Financial Answer Receipt, with the answer text composed by code from the tool results.

A question costs at most three Gemini calls (route, SQL draft, one SQL repair) and never loops.
Gemini never sees query results and never writes a figure. The SQL tool receives the user's
question unchanged, so a question answered before reuses the saved SQL draft instead of spending
quota.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from portfolioiq import timing
from portfolioiq.config import Settings
from portfolioiq.exposure import ExposureResult, SectorExposure, calculate_exposure
from portfolioiq.formatting import display_value, pct
from portfolioiq.llm import GeminiLLM, LLMCall, LLMError, StructuredLLM
from portfolioiq.receipt import (Clarification, DataBasis, FinancialAnswerReceipt, ModelUsage, ReceiptStatus, Route,
                                 SectorComparison, SectorRank, Timings, ToolCall)
from portfolioiq.resolver import (SECTOR_ALIASES, Candidate, Catalog, PortfolioName, Resolution, normalise,
                                  resolve_portfolio)
from portfolioiq.router import ROUTER_INSTRUCTION, RouterDecision, router_context, router_prompt, router_version
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_engine import SqlExecution, data_fingerprint, ensure_reference_db, open_read_only
from portfolioiq.sql_tool import SqlTool, SqlToolResult, input_problem, variant_for
from portfolioiq.verification import Check, CheckStatus, VerificationStatus

TIE_TOLERANCE = 1e-12
MAX_LISTED_VALUES = 15  # a one-column result up to this size is listed in the answer text
EXPOSURE_TABLES = ("holdings", "portfolios", "sectors", "securities")
SECTOR_FILLER = frozenset({"sector", "sectors", "stock", "stocks", "exposure"})

SQL_STATUS: dict[str, ReceiptStatus] = {
    "ok": "answered", "no_rows": "no_data", "rejected": "rejected", "unanswerable": "unanswerable",
    "needs_clarification": "needs_clarification", "invalid_input": "invalid_input", "invalid_sql": "error",
    "execution_error": "error", "timeout": "error", "llm_error": "error",
}
RESOLUTION_STATUS: dict[str, ReceiptStatus] = {
    "needs_clarification": "needs_clarification", "not_found": "not_found", "rejected": "rejected",
    "invalid_input": "needs_clarification",
}
DECLINED = {
    "out_of_scope": "PortfolioIQ answers questions about the portfolio database: portfolios, holdings, securities, "
                    "sectors, transactions, prices, performance and risk. This question is outside that scope, so "
                    "no tool was run.",
    "modifies_data": "PortfolioIQ is read-only: it cannot add, change or delete data. Nothing was run.",
}
BASIS_SQL = "Composed by code from the SQL result. Gemini wrote the query, not the answer."
BASIS_EXPOSURE = ("Composed by code from the exposure engine. Gemini chose the tool and passed the portfolio "
                  "reference; it wrote no figures.")
BASIS_HYBRID = "Composed by code from the SQL result and the exposure engine. Gemini wrote no figures."
BASIS_ROUTER = "Gemini's routing decision. No tool was run."


@dataclass
class _Trace:
    """Everything that happened while answering one question, in order."""

    question: str
    # The total-latency clock, independent of every stage clock (looked up at call time so tests can replace it).
    started: int = field(default_factory=lambda: timing.now_ns())
    router: RouterDecision | None = None
    router_calls: list[LLMCall] = field(default_factory=list)
    failed_requests: int = 0
    sql: SqlToolResult | None = None
    resolutions: list[Resolution] = field(default_factory=list)
    exposures: list[ExposureResult] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    exposure_ms: float | None = None  # None until an exposure calculation runs

    def record(self, tool: str, arguments: dict, status: str, resolved: dict | None = None,
               latency_ms: float | None = None, llm_calls: Sequence[LLMCall] = ()) -> None:
        self.tool_calls.append(ToolCall(step=len(self.tool_calls) + 1, tool=tool, arguments=arguments,
                                        resolved=resolved or {}, status=status,
                                        latency_ms=timing.round_ms(latency_ms), llm_calls=list(llm_calls)))


class PortfolioAgent:
    def __init__(self, db_path: Path, context: SemanticContext, llm: StructuredLLM | None, *,
                 sql_tool: SqlTool | None = None, fingerprint: str = "", thinking_level: str | None = None,
                 unavailable_reason: str | None = None):
        self.db_path = db_path
        self.context = context
        self.llm = llm
        self.unavailable_reason = unavailable_reason
        self.sql_tool = sql_tool or SqlTool(db_path, context, llm, unavailable_reason=unavailable_reason)
        self.fingerprint = fingerprint
        self.thinking_level = thinking_level
        conn = open_read_only(db_path)
        try:
            self.catalog = Catalog.from_db(conn)
            self.sectors = [name for (name,) in conn.execute("SELECT sector_name FROM sectors ORDER BY sector_name")]
        finally:
            conn.close()
        self.router_context = router_context(self.catalog, self.sectors)
        self.router_version = router_version(self.router_context)

    @classmethod
    def from_settings(cls, settings: Settings, *, llm: StructuredLLM | None = None) -> PortfolioAgent:
        """The agent on the reference database. ``llm`` replaces the live Gemini client (demo replay, tests)."""
        db_path = ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir)
        configuration = variant_for(settings.sql_prompt_variant)
        context = SemanticContext.from_db(db_path, examples=configuration.examples, latest_rule=configuration.latest_rule)
        reason = None
        if llm is None:
            try:
                llm = GeminiLLM(settings)
            except LLMError as exc:
                reason = exc.message
        sql_tool = SqlTool(db_path, context, llm, timeout_s=settings.sql_timeout_s, max_rows=settings.sql_max_rows,
                           unavailable_reason=reason, variant=settings.sql_prompt_variant)
        return cls(db_path, context, llm, sql_tool=sql_tool,
                   fingerprint=data_fingerprint(settings.data_dir, settings.schema_path),
                   thinking_level=settings.gemini_thinking_level, unavailable_reason=reason)

    def ask(self, question: str | None, *, label: str = "") -> FinancialAnswerReceipt:
        """Answer one question. ``label`` tags its Gemini requests in the API usage ledger."""
        trace = _Trace(question=(question or "").strip())
        problem = input_problem(trace.question)
        if problem:
            return self._receipt(trace, "invalid_input", "none", problem, "No tool was run: the question was not accepted.")
        if self.llm is None:
            return self._receipt(trace, "error", "none", self.unavailable_reason or "Gemini is not configured.",
                                 "No tool was run: Gemini is not available.", error_kind="not_configured")
        try:
            routed = self.llm.generate(system=ROUTER_INSTRUCTION, prompt=router_prompt(self.router_context, trace.question),
                                       schema=RouterDecision, purpose=f"{label} agent route".strip())
        except LLMError as exc:
            trace.failed_requests += exc.attempts
            trace.record("router", {"question": trace.question}, "llm_error")
            return self._receipt(trace, "error", "none", exc.message, "No tool was run: the routing request failed.",
                                 error_kind=exc.kind)
        decision = trace.router = routed.value
        trace.router_calls.append(routed.call)
        trace.record("router", {"question": trace.question}, "ok", resolved={"action": decision.action},
                     latency_ms=routed.call.latency_ms, llm_calls=[routed.call])
        handlers = {"sql": self._sql, "exposure": self._exposure, "exposure_for_sql_results": self._hybrid,
                    "clarify": self._clarify, "decline": self._decline}
        return handlers[decision.action](trace, decision, label)

    # --- routes ----------------------------------------------------------------------------------

    def _sql(self, trace: _Trace, decision: RouterDecision, label: str) -> FinancialAnswerReceipt:
        result = self._run_sql(trace, trace.question, label)
        status = SQL_STATUS[result.status]
        answered = status in ("answered", "no_data")
        clarification = (Clarification(question=result.message, source="sql_tool")
                         if result.status == "needs_clarification" else None)
        return self._receipt(trace, status, "sql", _sql_answer(result) if answered else result.message,
                             BASIS_SQL if answered else f"No result: the SQL tool's status is {result.status}.",
                             verification=result.verification, checks=result.checks, caveats=result.caveats,
                             clarification=clarification, error_kind=_sql_error_kind(result))

    def _exposure(self, trace: _Trace, decision: RouterDecision, label: str) -> FinancialAnswerReceipt:
        phrase = decision.portfolio.strip()
        if not phrase:
            trace.record("sector_exposure", {"portfolio": ""}, "needs_clarification")
            clarification = Clarification(
                question="Which portfolio should the sector exposure be calculated for?", source="resolver",
                candidates=[Candidate(kind="portfolio", name=p.name, portfolio_id=p.portfolio_id,
                                      reason="portfolio in the database") for p in self.catalog.portfolios])
            return self._receipt(trace, "needs_clarification", "exposure", clarification.question,
                                 "No tool result: no portfolio was named.", clarification=clarification)
        resolution = resolve_portfolio(self.catalog, phrase, question=trace.question)
        trace.resolutions.append(resolution)
        if resolution.status != "resolved":
            trace.record("sector_exposure", {"portfolio": phrase}, resolution.status, resolved=_summary(resolution))
            return self._unresolved(trace, "exposure", resolution)
        result = self._calculate(trace, resolution, phrase)
        if result.status == "invalid_data":
            return self._receipt(trace, "error", "exposure", result.message, "No figures: the source weights are invalid.",
                                 verification=result.verification, checks=result.checks, error_kind="invalid_source_data")
        return self._receipt(trace, "answered", "exposure", _exposure_answer(result, resolution.sector_terms),
                             BASIS_EXPOSURE, verification=result.verification, checks=result.checks,
                             caveats=result.caveats)

    def _hybrid(self, trace: _Trace, decision: RouterDecision, label: str) -> FinancialAnswerReceipt:
        route: Route = "exposure_for_sql_results"
        sql_question = decision.sql_question.strip()
        if not sql_question:
            return self._receipt(trace, "error", route, "The routing decision did not say which portfolios to compare, "
                                 "so nothing was run.", "No tool was run: the routing decision was incomplete.",
                                 error_kind="incomplete_route")
        result = self._run_sql(trace, sql_question, label)
        if result.status != "ok":
            answer = ("The SQL step found no portfolios matching the condition, so no exposure was calculated."
                      if result.status == "no_rows" else result.message)
            return self._receipt(trace, SQL_STATUS[result.status], route, answer,
                                 f"No exposure result: the SQL step's status is {result.status}.",
                                 verification=result.verification, checks=result.checks, caveats=result.caveats,
                                 error_kind=_sql_error_kind(result))
        portfolios = _portfolios_in(result.execution, self.catalog)
        names = ", ".join(p.name for p in portfolios)
        identified = Check(name="Portfolios identified from the SQL result",
                           status=CheckStatus.PASS if portfolios else CheckStatus.FAIL,
                           detail=f"{len(portfolios)} exact portfolio name(s): {names}." if portfolios
                           else "No column of the result holds portfolio names or ids.")
        if not portfolios:
            return self._receipt(trace, "error", route, "The SQL step did not return portfolio names, so sector "
                                 "exposure could not be calculated.", "No exposure result: the SQL result named no portfolio.",
                                 checks=[*result.checks, identified], caveats=result.caveats,
                                 error_kind="no_portfolios_in_result")
        listed = ["; ".join(p.name for p in portfolios)]  # names from a tool result ground the resolution (B5)
        for portfolio in portfolios:
            resolution = resolve_portfolio(self.catalog, portfolio.name, question=trace.question, context=listed)
            trace.resolutions.append(resolution)
            if resolution.status == "resolved":
                self._calculate(trace, resolution, portfolio.name)
        sector, note = self._sector_focus(decision.sector, trace.resolutions)
        comparison = _compare(sector, trace.exposures) if sector else None
        checks = [*result.checks, identified, *(_reconciled(e) for e in trace.exposures)]
        failed = (result.verification is VerificationStatus.VERIFICATION_FAILED
                  or any(c.status is CheckStatus.FAIL for c in checks))
        caveats = _unique([*result.caveats, *([note] if note else []),
                           *(f"{e.portfolio_name}: {c}" for e in trace.exposures for c in e.caveats)])
        return self._receipt(trace, "answered", route, _hybrid_answer(portfolios, trace.exposures, comparison, sector),
                             BASIS_HYBRID, checks=checks, caveats=caveats, comparison=comparison,
                             verification=VerificationStatus.VERIFICATION_FAILED if failed
                             else VerificationStatus.RESULT_VALIDATED)

    def _clarify(self, trace: _Trace, decision: RouterDecision, label: str) -> FinancialAnswerReceipt:
        question = decision.clarification_question.strip() or "Could you say more precisely what you would like to know?"
        options = [o.strip() for o in decision.options if o.strip()][:5]
        clarification = Clarification(question=question, options=options, source="router")
        return self._receipt(trace, "needs_clarification", "clarify", question, BASIS_ROUTER, clarification=clarification)

    def _decline(self, trace: _Trace, decision: RouterDecision, label: str) -> FinancialAnswerReceipt:
        kind = decision.decline_kind if decision.decline_kind != "none" else "out_of_scope"
        status: ReceiptStatus = "rejected" if kind == "modifies_data" else "declined"
        return self._receipt(trace, status, "decline", DECLINED[kind], BASIS_ROUTER)

    # --- helpers ---------------------------------------------------------------------------------

    def _run_sql(self, trace: _Trace, question: str, label: str) -> SqlToolResult:
        result = self.sql_tool.answer(question, label=label)
        trace.sql = result
        trace.record("sql_query", {"question": question}, result.status, latency_ms=result.total_latency_ms,
                     resolved={"sql": result.sql, "row_count": result.execution.row_count if result.execution else None},
                     llm_calls=[a.llm for a in result.attempts])
        return result

    def _calculate(self, trace: _Trace, resolution: Resolution, proposed: str) -> ExposureResult:
        started = timing.now_ns()
        conn = open_read_only(self.db_path)
        try:
            result = calculate_exposure(conn, resolution.portfolio_id)
        finally:
            conn.close()
        elapsed = timing.elapsed_ms(started)
        trace.exposure_ms = (trace.exposure_ms or 0.0) + elapsed
        trace.exposures.append(result)
        trace.record("sector_exposure", {"portfolio": proposed}, result.status, resolved=_summary(resolution),
                     latency_ms=elapsed)
        return result

    def _unresolved(self, trace: _Trace, route: Route, resolution: Resolution) -> FinancialAnswerReceipt:
        ask_user = resolution.status == "needs_clarification" or bool(resolution.candidates)
        clarification = (Clarification(question=resolution.message, candidates=resolution.candidates, source="resolver")
                         if ask_user and resolution.status != "rejected" else None)
        return self._receipt(trace, RESOLUTION_STATUS[resolution.status], route, resolution.message,
                             f"No tool result: the portfolio reference was not resolved (rule {resolution.rule}).",
                             clarification=clarification)

    def _sector_focus(self, proposed: str, resolutions: list[Resolution]) -> tuple[str | None, str | None]:
        """The sector to rank by, and a caveat when there is none to use."""
        if proposed.strip():
            match = _match_sector(proposed, self.sectors)
            if match:
                return match, None
            return None, f"'{proposed.strip()}' is not a sector in the database, so the portfolios are not ranked."
        named = resolutions[0].sector_terms if resolutions else []
        if len(named) == 1:
            return named[0], None
        if named:
            return None, f"The question names several sectors ({', '.join(named)}), so the portfolios are not ranked."
        return None, None

    def _receipt(self, trace: _Trace, status: ReceiptStatus, route: Route, answer: str, basis: str, *,
                 verification: VerificationStatus = VerificationStatus.VERIFICATION_UNAVAILABLE,
                 checks: Sequence[Check] = (), caveats: Sequence[str] = (), clarification: Clarification | None = None,
                 error_kind: str | None = None, comparison: SectorComparison | None = None) -> FinancialAnswerReceipt:
        sql, calls = trace.sql, trace.router_calls
        tables = set(sql.validation.tables) if sql is not None and sql.validation is not None else set()
        if trace.exposures:
            tables |= set(EXPOSURE_TABLES)
        total_ms = timing.elapsed_ms(trace.started)
        planner_ms = timing.sum_measured(c.latency_ms for c in calls)
        sql_generation_ms = sql.llm_latency_ms if sql else None
        llm_ms = timing.sum_measured([planner_ms, sql_generation_ms])
        # Orchestration is the remainder once the nested intervals are taken out once each: the planner request,
        # the whole SQL tool step (its generation and execution lie inside it) and the exposure calculation.
        # A failed planner request is not timed here, so the remainder cannot be separated in that case.
        orchestration_ms = None
        if not trace.failed_requests:
            orchestration_ms = timing.round_ms(max(0.0, total_ms - (planner_ms or 0.0)
                                                   - ((sql.total_latency_ms or 0.0) if sql else 0.0)
                                                   - (trace.exposure_ms or 0.0)))
        usage = ModelUsage(
            model=_first(*(c.model for c in calls), sql.model if sql else None, getattr(self.llm, "model", None)),
            model_version=_first(sql.model_version if sql else None, *(c.model_version for c in calls)),
            sdk_version=_first(*(c.sdk_version for c in calls), sql.sdk_version if sql else None),
            thinking_level=self.thinking_level,
            api_requests=sum(c.attempts for c in calls) + trace.failed_requests + (sql.llm_calls if sql else 0),
            cache_hits=sum(1 for c in calls if c.source == "cache") + (sql.cache_hits if sql else 0),
            replayed_responses=sum(1 for c in calls if c.source == "replay") + (sql.replayed if sql else 0),
            prompt_tokens=sum(c.prompt_tokens or 0 for c in calls) + (sql.prompt_tokens if sql else 0),
            output_tokens=sum(c.output_tokens or 0 for c in calls) + (sql.output_tokens if sql else 0),
            thinking_tokens=sum(c.thinking_tokens or 0 for c in calls) + (sql.thinking_tokens if sql else 0),
            llm_latency_ms=llm_ms,
        )
        return FinancialAnswerReceipt(
            question=trace.question, status=status, route=route, answer=answer, answer_basis=basis,
            verification=verification, checks=list(checks), caveats=list(caveats), clarification=clarification,
            error_kind=error_kind, router=trace.router, resolutions=trace.resolutions, sql=sql,
            exposures=trace.exposures, comparison=comparison, tool_calls=trace.tool_calls, model=usage,
            data=DataBasis(data_fingerprint=self.fingerprint, context_version=self.context.version,
                           router_version=self.router_version, sql_prompt_version=self.sql_tool.prompt_version,
                           sql_prompt_variant=self.sql_tool.variant,
                           data_anchor_date=self.context.dates.dataset_anchor, tables=sorted(tables)),
            timings=Timings(total_latency_ms=timing.round_ms(total_ms), planner_latency_ms=planner_ms,
                            sql_generation_latency_ms=sql_generation_ms,
                            sql_execution_latency_ms=sql.sql_latency_ms if sql else None,
                            exposure_calculation_latency_ms=timing.round_ms(trace.exposure_ms),
                            orchestration_latency_ms=orchestration_ms),
        )


# --- answer text, composed from tool results only -----------------------------------------------

def _sql_answer(result: SqlToolResult) -> str:
    execution = result.execution
    if execution is None or execution.row_count == 0:
        return "The query ran and returned no rows."
    if execution.row_count == 1 and len(execution.columns) == 1:
        return f"{execution.columns[0]}: {display_value(execution.rows[0][0])}."
    truncated = ", truncated at the row limit" if execution.truncated else ""
    if len(execution.columns) == 1 and execution.row_count <= MAX_LISTED_VALUES:
        values = "; ".join(display_value(row[0]) for row in execution.rows)
        return f"{execution.row_count} value(s) of {execution.columns[0]}: {values}."
    return (f"{execution.row_count} row(s){truncated}, with columns {', '.join(execution.columns)}. "
            f"See the result table.")


def _exposure_answer(result: ExposureResult, focus: list[str]) -> str:
    if result.status != "ok":
        return result.message
    parts = [result.message]
    by_sector = {s.sector: s for s in result.sectors}
    if focus:
        for sector in focus:
            held = by_sector.get(sector)
            parts.append(f"{sector}: {_methods(held)}." if held
                         else f"{sector}: 0.00%; the portfolio holds no {sector} stocks.")
    else:
        top = _top_sectors(result)
        if len(top) == 1:
            parts.append(f"Largest sector: {top[0].sector}, {_methods(top[0])}.")
        else:
            parts.append(f"Largest sectors (tied): {' and '.join(s.sector for s in top)}, each {_methods(top[0])}.")
    for line in result.excluded:
        parts.append(f"{line.label}: {pct(line.weight)}% of the portfolio, outside both methods.")
    if result.residual_reported:
        parts.append(f"Unattributed weight residual: {pct(result.residual)}%; it is not cash (rule B3).")
    return " ".join(parts)


def _hybrid_answer(portfolios: list[PortfolioName], exposures: list[ExposureResult],
                   comparison: SectorComparison | None, sector: str | None) -> str:
    lead = f"The SQL step returned {len(portfolios)} portfolio(s): {', '.join(p.name for p in portfolios)}."
    if comparison is None:
        largest = "; ".join(f"{e.portfolio_name}: {_largest(e)}" for e in exposures)
        return f"{lead} Sector exposure was calculated for each. Largest sector by Method A: {largest}."
    if not comparison.leaders_method_a:
        return f"{lead} None of them holds {sector} stocks, so their {sector} exposure is 0.00%."
    rows = {r.portfolio_name: r for r in comparison.rows}
    top = rows[comparison.leaders_method_a[0]]
    if len(comparison.leaders_method_a) == 1:
        text = f"{lead} Highest {sector} exposure: {top.portfolio_name}, {pct(top.method_a)}% of the portfolio (Method A)"
        text += f" and {pct(top.method_b)}% of its equity sleeve (Method B)." if top.method_b is not None else "."
    else:
        text = (f"{lead} Tied for the highest {sector} exposure, at {pct(top.method_a)}% of the portfolio "
                f"(Method A): {' and '.join(comparison.leaders_method_a)}.")
    if comparison.leaders_method_b and set(comparison.leaders_method_b) != set(comparison.leaders_method_a):
        best = rows[comparison.leaders_method_b[0]]
        text += (f" By Method B (share of the equity sleeve) the highest is "
                 f"{' and '.join(comparison.leaders_method_b)}, at {pct(best.method_b)}%.")
    return text


def _methods(sector: SectorExposure) -> str:
    return (f"{pct(sector.method_a)}% of the portfolio (Method A) and {pct(sector.method_b)}% of the equity sleeve "
            f"(Method B)")


def _top_sectors(result: ExposureResult) -> list[SectorExposure]:
    best = result.sectors[0].method_a
    return [s for s in result.sectors if best - s.method_a <= TIE_TOLERANCE]


def _largest(result: ExposureResult) -> str:
    if result.status != "ok":
        return "no eligible stocks" if result.status == "no_eligible_stocks" else "not calculated (invalid data)"
    top = _top_sectors(result)
    return f"{' and '.join(s.sector for s in top)} {pct(top[0].method_a)}%"


# --- small deterministic helpers ----------------------------------------------------------------

def _portfolios_in(execution: SqlExecution | None, catalog: Catalog) -> list[PortfolioName]:
    """Portfolios named in a SQL result: the column with the most exact names, else a portfolio_id column."""
    if execution is None or not execution.rows:
        return []
    by_name = {p.name: p for p in catalog.portfolios}
    by_id = {p.portfolio_id: p for p in catalog.portfolios}
    best: list[PortfolioName] = []
    for index, column in enumerate(execution.columns):
        found = [by_name[row[index]] for row in execution.rows if isinstance(row[index], str) and row[index] in by_name]
        if not found and column.lower().endswith("portfolio_id"):
            found = [by_id[row[index]] for row in execution.rows
                     if isinstance(row[index], int) and row[index] in by_id]
        if len(found) > len(best):
            best = found
    return list(dict.fromkeys(best))  # each portfolio once, in result order


def _match_sector(text: str, sectors: list[str]) -> str | None:
    phrase = " ".join(w for w in normalise(text) if w not in SECTOR_FILLER)
    for sector in sectors:
        if phrase == " ".join(normalise(sector)):
            return sector
    alias = SECTOR_ALIASES.get(phrase)
    return alias if alias in sectors else None


def _compare(sector: str, exposures: list[ExposureResult]) -> SectorComparison:
    rows = []
    for result in exposures:
        held = next((s for s in result.sectors if s.sector == sector), None)
        rows.append(SectorRank(
            portfolio_id=result.portfolio_id, portfolio_name=result.portfolio_name,
            method_a=held.method_a if held else 0.0,
            method_b=held.method_b if held else (0.0 if result.status == "ok" else None),
            exposure_status=result.status))
    rows.sort(key=lambda r: (-r.method_a, -(r.method_b or 0.0), r.portfolio_name))
    return SectorComparison(sector=sector, rows=rows, leaders_method_a=leaders(rows, "method_a"),
                            leaders_method_b=leaders(rows, "method_b"))


def leaders(rows: list[SectorRank], attribute: str) -> list[str]:
    """Names sharing the highest value (a tie gives several); empty when no portfolio holds the sector."""
    values = [(getattr(r, attribute), r.portfolio_name) for r in rows if getattr(r, attribute) is not None]
    if not values:
        return []
    top = max(value for value, _ in values)
    return [name for value, name in values if top - value <= TIE_TOLERANCE] if top > 0 else []


def _reconciled(result: ExposureResult) -> Check:
    ok = result.verification is VerificationStatus.INDEPENDENTLY_RECONCILED
    return Check(name=f"Exposure reconciled: {result.portfolio_name}", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                 detail=result.reconciliation.detail, rule="B1 to B3")


def _summary(resolution: Resolution) -> dict:
    return {"status": resolution.status, "rule": resolution.rule, "portfolio_id": resolution.portfolio_id,
            "portfolio_name": resolution.portfolio_name, "grounded_in_question": resolution.grounded_in_question,
            "confirmation_required": resolution.confirmation_required}


def _sql_error_kind(result: SqlToolResult) -> str | None:
    if result.status == "llm_error":
        return result.error_kind or "llm_error"
    return result.status if result.status in ("invalid_sql", "execution_error", "timeout") else None


def _first(*values):
    return next((v for v in values if v is not None), None)


def _unique(items) -> list[str]:
    return list(dict.fromkeys(items))
