"""Financial Answer Receipt: the complete, auditable record of one answer.

Every reply from the agent carries a receipt, including clarifications, refusals and errors. It
records:

- what was asked;
- which tools ran, with their arguments as proposed by Gemini and as resolved by code;
- the SQL and its checks, and the exposure figures;
- the data the answer came from (data fingerprint and prompt versions);
- the provider, model and SDK that were used;
- what the answer cost in API requests, tokens and time.

The answer text is composed by code from tool results. Gemini never writes a figure.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from portfolioiq.timing import sum_measured
from portfolioiq.exposure import ExposureResult
from portfolioiq.llm import LLMCall
from portfolioiq.resolver import Candidate, Resolution
from portfolioiq.router import RouterDecision
from portfolioiq.sql_tool import SqlToolResult
from portfolioiq.verification import Check, VerificationStatus

PROVIDER = "Google Gemini API"
SDK_NAME = "google-genai"

ReceiptStatus = Literal["answered", "no_data", "needs_clarification", "not_found", "unanswerable", "declined",
                        "rejected", "invalid_input", "error"]
Route = Literal["sql", "exposure", "exposure_for_sql_results", "clarify", "decline", "none"]


class ToolCall(BaseModel):
    step: int
    tool: Literal["router", "sql_query", "sector_exposure"]
    arguments: dict[str, Any]  # as proposed by Gemini, or as passed by code
    resolved: dict[str, Any] = {}  # as resolved by deterministic code
    status: str
    latency_ms: float | None = None  # None: not measured (the step made no timed call)
    llm_calls: list[LLMCall] = []  # Gemini calls behind this step; each request_hash locates its raw response


class ModelUsage(BaseModel):
    provider: str = PROVIDER
    model: str | None = None  # the configured model; there is no silent fallback
    model_version: str | None = None  # as reported by the API
    sdk: str = SDK_NAME
    sdk_version: str | None = None
    thinking_level: str | None = None
    api_requests: int = 0  # requests sent to the API, failed ones included
    cache_hits: int = 0  # answers reused from saved responses, at no quota cost
    replayed_responses: int = 0  # DEMO / FIXTURE MODE: recorded Gemini responses replayed offline; nothing sent
    prompt_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    llm_latency_ms: float | None = None  # client-observed Gemini request time; None when no request was sent


class Clarification(BaseModel):
    question: str
    options: list[str] = []  # suggested by the router
    candidates: list[Candidate] = []  # found by the resolver
    source: Literal["router", "resolver", "sql_tool"]


class SectorRank(BaseModel):
    portfolio_id: int
    portfolio_name: str
    method_a: float  # share of the whole portfolio
    method_b: float | None  # share of the equity sleeve; None when there are no eligible stocks
    exposure_status: str


class SectorComparison(BaseModel):
    sector: str
    ranked_by: str = "Method A (share of the whole portfolio), then Method B"
    rows: list[SectorRank]
    leaders_method_a: list[str]  # more than one name means a tie; empty when no portfolio holds the sector
    leaders_method_b: list[str]


class DataBasis(BaseModel):
    data_fingerprint: str  # SHA-256 of the supplied CSV files and schema
    context_version: str  # the SQL tool's prompt context
    router_version: str  # the router's instruction and context
    sql_prompt_version: str | None = None  # the SQL tool's instruction and context
    sql_prompt_variant: str | None = None  # "baseline" or a named candidate; None in receipts recorded before variants
    data_anchor_date: str  # the latest date in the data; relative dates are resolved against it
    tables: list[str] = []


class Timings(BaseModel):
    """Measured durations of one answer, in milliseconds. None means the stage did not run or was not measured.

    `total_latency_ms` runs on its own clock from the accepted question to the finished answer. The stage
    values are intervals nested inside it and are not added to it: `sql_generation_latency_ms` and
    `sql_execution_latency_ms` both lie inside the SQL tool's time. `planner_latency_ms` and
    `sql_generation_latency_ms` are client-observed Gemini request times (network, queueing and inference
    together), not the model's inference time. `orchestration_latency_ms` is what is left of the total after
    the planner request, the whole SQL tool step and the exposure calculation: local code, plus any request
    pacing or retry waits.
    """

    latency_schema: int = 2  # 1: receipts recorded before 2026-11-09 (total/llm/sql/exposure only, 0.0 = not run)
    total_latency_ms: float | None = None
    planner_latency_ms: float | None = None
    sql_generation_latency_ms: float | None = None
    sql_execution_latency_ms: float | None = None
    exposure_calculation_latency_ms: float | None = None
    orchestration_latency_ms: float | None = None
    legacy_llm_ms: float | None = None  # schema 1 only: planner and SQL-generation requests combined

    @model_validator(mode="before")
    @classmethod
    def _read_schema_1(cls, data: Any) -> Any:
        """Load a receipt recorded with the old field names. Values are carried over exactly, never rescaled."""
        if isinstance(data, dict) and "total_ms" in data and "total_latency_ms" not in data:
            return {"latency_schema": 1, "total_latency_ms": data["total_ms"], "legacy_llm_ms": data.get("llm_ms"),
                    "sql_execution_latency_ms": data.get("sql_ms"),
                    "exposure_calculation_latency_ms": data.get("exposure_ms")}
        return data

    @property
    def llm_latency_ms(self) -> float | None:
        """All Gemini request time in the answer (planner + SQL generation)."""
        measured = sum_measured([self.planner_latency_ms, self.sql_generation_latency_ms])
        return measured if measured is not None else self.legacy_llm_ms

    def stages(self) -> list[tuple[str, float | None]]:
        """Display order: the overall latency first, then the stages that ran."""
        return [("Total application", self.total_latency_ms), ("Gemini request: routing", self.planner_latency_ms),
                ("Gemini request: SQL", self.sql_generation_latency_ms),
                ("SQL execution", self.sql_execution_latency_ms),
                ("Exposure calculation", self.exposure_calculation_latency_ms),
                ("Orchestration", self.orchestration_latency_ms)]


class FinancialAnswerReceipt(BaseModel):
    receipt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_utc: str = Field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    question: str
    status: ReceiptStatus
    route: Route
    answer: str
    answer_basis: str  # where the answer text came from
    verification: VerificationStatus
    checks: list[Check] = []
    caveats: list[str] = []
    clarification: Clarification | None = None
    error_kind: str | None = None
    router: RouterDecision | None = None
    resolutions: list[Resolution] = []
    sql: SqlToolResult | None = None
    exposures: list[ExposureResult] = []
    comparison: SectorComparison | None = None
    tool_calls: list[ToolCall] = []
    model: ModelUsage
    data: DataBasis
    timings: Timings

    def _material(self) -> dict:
        """Everything the answer says and rests on. Ids, timestamps, latencies and tokens are left out, so identical answers match."""
        sql = self.sql
        return {"question": self.question, "route": self.route, "status": self.status, "answer": self.answer, "answer_basis": self.answer_basis,
                "verification": self.verification.value, "checks": [c.model_dump(mode="json") for c in self.checks], "caveats": self.caveats,
                "error_kind": self.error_kind, "sql": sql.sql if sql else None, "columns": sql.execution.columns if sql and sql.execution else None,
                "rows": sql.execution.rows if sql and sql.execution else None,
                "financial_verification": sql.financial_verification.model_dump(mode="json") if sql and sql.financial_verification else None,
                "exposures": [e.model_dump(mode="json") for e in self.exposures], "data": self.data.model_dump(mode="json"),
                "model": self.model.model, "model_version": self.model.model_version}

    def fingerprint(self) -> str:
        """SHA-256 of the receipt's material content. It detects a changed field; it is not a signature and proves no author."""
        return hashlib.sha256(json.dumps(self._material(), sort_keys=True, default=str).encode()).hexdigest()

    def assurance(self) -> dict[str, str]:
        """Separate answers to separate questions. A query can run correctly and still be unverified."""
        sql = self.sql
        return {"sql_execution": sql.status if sql else "not run", "result_checks": self.verification.value,
                "independent_verification": sql.financial_verification.status.value if sql and sql.financial_verification else
                ("INDEPENDENTLY_RECONCILED" if self.exposures and all(e.verification.value == "INDEPENDENTLY_RECONCILED" for e in self.exposures) else "not applicable"),
                "answer_text": "composed by code from tool results" if self.status in ("answered", "no_data") else "no figures stated"}


def receipt_is_intact(payload: dict, stored_fingerprint: str) -> bool:
    """True when a saved receipt still matches the fingerprint recorded for it."""
    return FinancialAnswerReceipt.model_validate(payload).fingerprint() == stored_fingerprint
