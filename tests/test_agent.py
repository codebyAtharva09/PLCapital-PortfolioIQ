"""Milestone 4: the Portfolio Agent routes with Gemini, answers with deterministic tools and records a receipt.

A fake LLM returns scripted routing decisions and SQL drafts, so these tests need no network and
cost no quota. An unexpected extra request fails the test.
"""

import hashlib
import json
import re

import pytest

from portfolioiq.agent import PortfolioAgent, leaders
from portfolioiq.config import PROJECT_ROOT
from portfolioiq.exposure import calculate_exposure
from portfolioiq.formatting import pct
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.receipt import FinancialAnswerReceipt, SectorRank
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_engine import open_read_only
from portfolioiq.sql_tool import SqlDraft, SqlTool
from portfolioiq.verification import CheckStatus, VerificationStatus

FIXTURES = PROJECT_ROOT / "evaluation" / "fixtures"
HIGH_RISK = "SELECT portfolio_name FROM portfolios WHERE target_risk_level = 'High'"


class FakeLLM:
    """Scripted structured output. A one-item tuple stands for a response served from the saved-response cache."""

    model = "fake-model"

    def __init__(self, scripted):
        self.scripted, self.requests = list(scripted), []

    def generate(self, *, system, prompt, schema, purpose=""):
        self.requests.append({"schema": schema.__name__, "prompt": prompt, "purpose": purpose})
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        cached = isinstance(item, tuple)
        value = item[0] if cached else item
        assert isinstance(value, schema), f"expected {schema.__name__}, got {type(value).__name__}"
        if cached:
            call = LLMCall(model="fake-model", model_version="fake-model-001", sdk_version="test", source="cache",
                           latency_ms=None, attempts=0)
        else:
            call = LLMCall(model="fake-model", model_version="fake-model-001", sdk_version="test", latency_ms=15.0,
                           attempts=1, prompt_tokens=100, output_tokens=10, thinking_tokens=2)
        return LLMResult(value=value, call=call)


def route(action, **fields):
    values = dict(portfolio="", sql_question="", sector="", clarification_question="", options=[],
                  decline_kind="none", reason="Scripted for the test.")
    values.update(fields)
    return RouterDecision(action=action, **values)


def draft(sql, explanation="Scripted query."):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation=explanation)


def hybrid(sector="Technology", sql_question="List the names of high-risk portfolios"):
    return route("exposure_for_sql_results", sql_question=sql_question, sector=sector)


@pytest.fixture(scope="module")
def context(readonly_db):
    return SemanticContext.from_db(readonly_db)


@pytest.fixture
def make(readonly_db, context):
    def build(*scripted):
        llm = FakeLLM(scripted)
        return PortfolioAgent(readonly_db, context, llm, fingerprint="a" * 64, thinking_level="LOW"), llm
    return build


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- SQL route --------------------------------------------------------------------------------

def test_sql_route_answers_with_a_complete_receipt(make):
    agent, llm = make(route("sql"), draft("SELECT COUNT(*) AS portfolio_count FROM portfolios", "Counts all portfolios."))
    receipt = agent.ask("How many portfolios are there?")
    assert (receipt.status, receipt.route, receipt.answer) == ("answered", "sql", "portfolio_count: 13.")
    assert receipt.verification is VerificationStatus.RESULT_VALIDATED
    assert receipt.sql.sql == "SELECT COUNT(*) AS portfolio_count FROM portfolios"
    assert [c.tool for c in receipt.tool_calls] == ["router", "sql_query"]
    assert [r["schema"] for r in llm.requests] == ["RouterDecision", "SqlDraft"]
    model = receipt.model
    assert (model.provider, model.model, model.model_version, model.sdk, model.thinking_level) == (
        "Google Gemini API", "fake-model", "fake-model-001", "google-genai", "LOW")
    assert (model.api_requests, model.cache_hits, model.prompt_tokens, model.output_tokens, model.thinking_tokens) == (
        2, 0, 200, 20, 4)
    data = receipt.data
    assert (data.data_fingerprint, data.tables, data.data_anchor_date) == ("a" * 64, ["portfolios"], "2023-10-09")
    assert data.context_version == agent.context.version and data.router_version == agent.router_version
    assert data.sql_prompt_version == agent.sql_tool.prompt_version
    assert [len(c.llm_calls) for c in receipt.tool_calls] == [1, 1]  # each step keeps its own Gemini call
    assert "Gemini wrote the query, not the answer" in receipt.answer_basis


def test_sql_prompt_is_identical_to_the_sql_tools_own_so_saved_drafts_are_reused(make, readonly_db, context):
    question = "What are the names of all active portfolios?"
    sql = "SELECT portfolio_name FROM portfolios WHERE status = 'Active'"
    agent, via_agent = make(route("sql"), draft(sql))
    agent.ask(question, label="Q2")
    direct = FakeLLM([draft(sql)])
    SqlTool(readonly_db, context, direct).answer(question)
    assert via_agent.requests[1]["prompt"] == direct.requests[0]["prompt"]
    assert [r["purpose"] for r in via_agent.requests] == ["Q2 agent route", "Q2 sql_tool draft 1"]


def test_cached_sql_draft_costs_no_request(make):
    agent, _ = make(route("sql"), (draft("SELECT COUNT(*) FROM portfolios"),))
    receipt = agent.ask("How many portfolios do we have in total?")
    assert receipt.status == "answered"
    assert (receipt.model.api_requests, receipt.model.cache_hits) == (1, 1)


def test_one_column_results_are_listed_in_the_answer(make):
    agent, _ = make(route("sql"), draft("SELECT portfolio_name FROM portfolios WHERE status = 'Passive'"))
    receipt = agent.ask("Which portfolios are passive?")
    assert receipt.answer == ("3 value(s) of portfolio_name: Total Stock Market Index Fund; "
                              "Total Bond Market Index Fund; Total International Index Fund.")


def test_empty_result_is_no_data(make):
    agent, _ = make(route("sql"), draft("SELECT * FROM transactions WHERE transaction_date < '2000-01-01'"))
    receipt = agent.ask("Show transactions from before 2000")
    assert (receipt.status, receipt.answer) == ("no_data", "The query ran and returned no rows.")


@pytest.mark.parametrize("reason, status", [
    ("ambiguous", "needs_clarification"), ("not_in_data", "unanswerable"), ("modifies_data", "rejected")])
def test_sql_tool_refusals_become_receipt_statuses(make, reason, status):
    agent, _ = make(route("sql"), SqlDraft(answerable=False, decline_reason=reason, sql="", explanation="Cannot answer."))
    receipt = agent.ask("A question the SQL tool declines")
    assert (receipt.status, receipt.answer) == (status, "Cannot answer.")
    assert (receipt.clarification is not None) == (status == "needs_clarification")


def test_misrouted_destructive_request_is_still_stopped_by_the_sql_guard(make, readonly_db):
    before = file_hash(readonly_db)
    agent, _ = make(route("sql"), draft("DELETE FROM transactions WHERE transaction_date < '2023-10-01'"))
    receipt = agent.ask("Remove the old trades")
    assert receipt.status == "rejected" and receipt.sql.execution is None
    assert file_hash(readonly_db) == before


def test_sql_that_never_validates_is_an_error_with_its_kind(make):
    agent, _ = make(route("sql"), draft("SELECT nope FROM portfolios"), draft("SELECT still_nope FROM portfolios"))
    receipt = agent.ask("List the portfolio names")
    assert (receipt.status, receipt.error_kind, receipt.model.api_requests) == ("error", "invalid_sql", 3)


# --- exposure route ---------------------------------------------------------------------------

def test_exposure_route_matches_the_hand_derived_q9_fixture(make):
    expected = fixture("q09_tech_innovation_fund.json")
    agent, llm = make(route("exposure", portfolio="Tech Innovation Fund"))
    receipt = agent.ask("What are the sector exposures for the Tech Innovation Fund?")
    assert (receipt.status, receipt.route, len(llm.requests), receipt.sql) == ("answered", "exposure", 1, None)
    (result,) = receipt.exposures
    assert result.portfolio_name == expected["portfolio_name"]
    assert {s.sector: s.method_a for s in result.sectors} == pytest.approx(expected["expected"]["method_a"])
    assert {s.sector: s.method_b for s in result.sectors} == pytest.approx(expected["expected"]["method_b"])
    assert receipt.verification is VerificationStatus.INDEPENDENTLY_RECONCILED
    assert ("Largest sector: Technology, 100.00% of the portfolio (Method A) and 100.00% of the equity sleeve "
            "(Method B).") in receipt.answer
    call = receipt.tool_calls[1]
    assert (call.tool, call.arguments, call.resolved["rule"]) == ("sector_exposure", {"portfolio": "Tech Innovation Fund"}, "B5.1")


def test_q10_phrase_is_passed_through_and_resolved_by_code(make):
    expected = fixture("q10_international_equity_fund.json")
    agent, _ = make(route("exposure", portfolio="international equity"))
    receipt = agent.ask("Calculate the sector exposure breakdown for international equity")
    (resolution,) = receipt.resolutions
    assert (resolution.portfolio_name, resolution.rule, resolution.grounded_in_question) == (
        "International Equity Fund", "B5.1", True)
    assert {s.sector: s.method_a for s in receipt.exposures[0].sectors} == pytest.approx(expected["expected"]["method_a"])


def test_answer_leads_with_a_sector_named_in_the_question(make):
    agent, _ = make(route("exposure", portfolio="Balanced Portfolio"))
    receipt = agent.ask("What is the tech exposure of the Balanced Portfolio?")
    assert "Technology: 22.00% of the portfolio (Method A) and 61.11% of the equity sleeve (Method B)." in receipt.answer
    assert "Bonds (excluded): 64.00% of the portfolio, outside both methods." in receipt.answer


def test_a_named_sector_that_is_not_held_is_reported_as_zero(make):
    agent, _ = make(route("exposure", portfolio="Tech Innovation Fund"))
    receipt = agent.ask("How much Energy exposure does the Tech Innovation Fund have?")
    assert "Energy: 0.00%; the portfolio holds no Energy stocks." in receipt.answer


def test_bond_only_portfolio_says_there_are_no_eligible_stocks(make):
    agent, _ = make(route("exposure", portfolio="Fixed Income Plus"))
    receipt = agent.ask("Show the sector allocation of Fixed Income Plus")
    assert receipt.status == "answered" and receipt.exposures[0].status == "no_eligible_stocks"
    assert "holds no eligible stocks" in receipt.answer


def test_unattributed_residual_is_shown_and_never_called_cash(make):
    agent, _ = make(route("exposure", portfolio="Total Stock Market Index Fund"))
    receipt = agent.ask("Sector allocation of the Total Stock Market Index Fund")
    assert "Unattributed weight residual: 2.00%; it is not cash (rule B3)." in receipt.answer


def test_answer_figures_all_come_from_the_exposure_engine(make):
    agent, _ = make(route("exposure", portfolio="Balanced Portfolio"))
    receipt = agent.ask("How is the Balanced Portfolio allocated across sectors?")
    (result,) = receipt.exposures
    computed = ({pct(v) for s in result.sectors for v in (s.method_a, s.method_b)}
                | {pct(e.weight) for e in result.excluded} | {pct(result.eligible_stock_weight)})
    shown = set(re.findall(r"\d+\.\d{2}", receipt.answer))
    assert shown and shown <= computed


def test_generic_reference_asks_which_portfolio(make):
    agent, llm = make(route("exposure", portfolio="international portfolio"))
    receipt = agent.ask("Sector exposure for the international portfolio")
    assert receipt.status == "needs_clarification" and receipt.exposures == [] and len(llm.requests) == 1
    assert [c.name for c in receipt.clarification.candidates] == ["International Equity Fund",
                                                                  "Total International Index Fund"]
    assert receipt.clarification.source == "resolver" and receipt.resolutions[0].rule == "B5.5"


def test_portfolio_not_named_in_the_question_is_never_used_silently(make):
    agent, _ = make(route("exposure", portfolio="Growth Equity Fund"))
    receipt = agent.ask("Show me the sector exposure of my fund")
    assert receipt.status == "needs_clarification" and receipt.exposures == []
    assert receipt.resolutions[0].rule == "B5 grounding check"


def test_typo_gets_a_suggestion_not_a_silent_correction(make):
    agent, _ = make(route("exposure", portfolio="Tech Inovation Fund"))
    receipt = agent.ask("Tech Inovation Fund exposure")
    assert receipt.status == "not_found" and receipt.exposures == []
    assert [c.name for c in receipt.clarification.candidates] == ["Tech Innovation Fund"]


def test_sql_in_the_portfolio_argument_is_rejected_before_matching(make, readonly_db):
    before = file_hash(readonly_db)
    agent, _ = make(route("exposure", portfolio="Growth Equity Fund'; DROP TABLE holdings;--"))
    receipt = agent.ask("Exposure for Growth Equity Fund'; DROP TABLE holdings;--")
    assert (receipt.status, receipt.resolutions[0].rule) == ("rejected", "SR1")
    assert receipt.exposures == [] and receipt.clarification is None
    assert file_hash(readonly_db) == before


def test_missing_portfolio_argument_lists_every_portfolio(make):
    agent, _ = make(route("exposure", portfolio=""))
    receipt = agent.ask("Show me a sector breakdown")
    assert receipt.status == "needs_clarification" and len(receipt.clarification.candidates) == 13


def test_tech_alias_in_portfolio_position_resolves(make):
    agent, _ = make(route("exposure", portfolio="tech fund"))
    receipt = agent.ask("Break down the tech fund by sector")
    assert receipt.exposures[0].portfolio_name == "Tech Innovation Fund" and receipt.resolutions[0].rule == "B5.2"


# --- SQL, then exposure for each portfolio found ----------------------------------------------

def test_hybrid_question_runs_sql_then_exposure_for_each_portfolio(make, readonly_db):
    agent, llm = make(hybrid(), draft(HIGH_RISK))
    receipt = agent.ask("Which high-risk portfolio has the largest Technology exposure?")
    assert (receipt.status, receipt.route) == ("answered", "exposure_for_sql_results")
    assert [r["schema"] for r in llm.requests] == ["RouterDecision", "SqlDraft"]
    assert [c.tool for c in receipt.tool_calls] == ["router", "sql_query"] + ["sector_exposure"] * 4
    assert receipt.sql.question == "List the names of high-risk portfolios"
    comparison = receipt.comparison
    assert (comparison.sector, comparison.leaders_method_a) == ("Technology", ["Tech Innovation Fund"])
    assert comparison.rows[0].portfolio_name == "Tech Innovation Fund"
    assert ("Highest Technology exposure: Tech Innovation Fund, 100.00% of the portfolio (Method A) and 100.00% "
            "of its equity sleeve (Method B).") in receipt.answer
    assert receipt.verification is VerificationStatus.RESULT_VALIDATED
    assert all(r.grounded_in_question for r in receipt.resolutions)  # grounded by the SQL result's names
    conn = open_read_only(readonly_db)
    try:
        for row in comparison.rows:
            tech = [s for s in calculate_exposure(conn, row.portfolio_id).sectors if s.sector == "Technology"]
            assert row.method_a == (tech[0].method_a if tech else 0.0)
    finally:
        conn.close()


def test_a_forced_reconciliation_failure_fails_the_whole_hybrid_answer(make, monkeypatch):
    """The independent check, not Gemini, decides: one portfolio that does not reconcile must not be reported as validated."""
    real = calculate_exposure
    calls = []

    def one_broken(conn, portfolio_id):
        result = real(conn, portfolio_id)
        calls.append(portfolio_id)
        return result.model_copy(update={"verification": VerificationStatus.VERIFICATION_FAILED}) if len(calls) == 2 else result

    monkeypatch.setattr("portfolioiq.agent.calculate_exposure", one_broken)
    agent, _ = make(hybrid(), draft(HIGH_RISK))
    receipt = agent.ask("Which high-risk portfolio has the largest Technology exposure?")
    assert receipt.verification is VerificationStatus.VERIFICATION_FAILED
    assert [c.name for c in receipt.checks if c.status is CheckStatus.FAIL] != []


def test_hybrid_uses_the_sector_in_the_question_when_the_router_leaves_it_empty(make):
    agent, _ = make(hybrid(sector=""), draft(HIGH_RISK))
    receipt = agent.ask("Which high-risk portfolio has the largest Technology exposure?")
    assert receipt.comparison.sector == "Technology"


def test_hybrid_with_an_unknown_sector_is_not_ranked(make):
    agent, _ = make(hybrid(sector="Crypto"), draft(HIGH_RISK))
    receipt = agent.ask("Which high-risk portfolio has the most crypto exposure?")
    assert receipt.status == "answered" and receipt.comparison is None and len(receipt.exposures) == 4
    assert any("'Crypto' is not a sector in the database" in c for c in receipt.caveats)


def test_hybrid_says_so_when_no_portfolio_holds_the_sector(make):
    agent, _ = make(hybrid(sector="Utilities", sql_question="List the bond-only portfolios"),
                    draft("SELECT portfolio_name FROM portfolios WHERE portfolio_id IN (8, 12)"))
    receipt = agent.ask("Which bond fund has the most Utilities exposure?")
    assert receipt.comparison.leaders_method_a == [] and "None of them holds Utilities stocks" in receipt.answer
    assert [r.method_b for r in receipt.comparison.rows] == [None, None]


def test_hybrid_accepts_portfolio_ids(make):
    agent, _ = make(hybrid(sector="Energy", sql_question="List international-strategy portfolios"),
                    draft("SELECT portfolio_id FROM portfolios WHERE strategy_type = 'International'"))
    receipt = agent.ask("Which international-strategy portfolio has the most Energy exposure?")
    assert [e.portfolio_name for e in receipt.exposures] == ["International Equity Fund", "Emerging Markets Fund"]


def test_hybrid_with_no_matching_portfolios_is_no_data(make):
    agent, _ = make(hybrid(sql_question="List portfolios with AUM over a trillion"),
                    draft("SELECT portfolio_name FROM portfolios WHERE total_aum > 1e12"))
    receipt = agent.ask("Which trillion-dollar portfolio has the most Technology exposure?")
    assert receipt.status == "no_data" and receipt.exposures == []


def test_hybrid_needs_portfolio_names_in_the_sql_result(make):
    agent, _ = make(hybrid(sql_question="How many portfolios are high risk?"),
                    draft("SELECT COUNT(*) FROM portfolios WHERE target_risk_level = 'High'"))
    receipt = agent.ask("Which high-risk portfolio has the largest Technology exposure?")
    assert (receipt.status, receipt.error_kind) == ("error", "no_portfolios_in_result")
    assert receipt.checks[-1].status is CheckStatus.FAIL and receipt.exposures == []


def test_incomplete_hybrid_decision_runs_nothing(make):
    agent, llm = make(hybrid(sql_question=""))
    receipt = agent.ask("Which high-risk portfolio has the largest Technology exposure?")
    assert (receipt.status, receipt.error_kind, len(llm.requests)) == ("error", "incomplete_route", 1)


def test_ties_are_reported_not_broken_silently():
    rows = [SectorRank(portfolio_id=1, portfolio_name="A", method_a=0.2, method_b=0.5, exposure_status="ok"),
            SectorRank(portfolio_id=2, portfolio_name="B", method_a=0.2, method_b=0.4, exposure_status="ok"),
            SectorRank(portfolio_id=3, portfolio_name="C", method_a=0.0, method_b=None,
                       exposure_status="no_eligible_stocks")]
    assert leaders(rows, "method_a") == ["A", "B"]
    assert leaders(rows, "method_b") == ["A"]
    assert leaders(rows[2:], "method_a") == []  # nobody holds the sector


# --- clarify and decline ----------------------------------------------------------------------

def test_clarify_route_asks_and_runs_no_tool(make):
    options = ["1-year return", "AUM", "Sharpe ratio", "volatility", "fees", "one too many"]
    agent, llm = make(route("clarify", clarification_question="Best by which measure?", options=options))
    receipt = agent.ask("Which portfolio is the best?")
    assert (receipt.status, receipt.route, receipt.answer) == ("needs_clarification", "clarify", "Best by which measure?")
    assert receipt.clarification.options == options[:5]
    assert len(llm.requests) == 1 and receipt.sql is None and receipt.exposures == []


@pytest.mark.parametrize("kind, status", [("out_of_scope", "declined"), ("modifies_data", "rejected")])
def test_declines_run_nothing_and_leave_the_database_unchanged(make, readonly_db, kind, status):
    before = file_hash(readonly_db)
    agent, llm = make(route("decline", decline_kind=kind))
    receipt = agent.ask("Some request")
    assert (receipt.status, receipt.route, len(llm.requests)) == (status, "decline", 1)
    assert receipt.verification is VerificationStatus.VERIFICATION_UNAVAILABLE
    assert file_hash(readonly_db) == before


# --- failures ---------------------------------------------------------------------------------

def test_router_failure_is_a_safe_error_that_counts_the_failed_request(make):
    agent, llm = make(LLMError("quota_exhausted", "Gemini API error 429 RESOURCE_EXHAUSTED: daily quota", 54000.0,
                               attempts=1))
    receipt = agent.ask("How many portfolios are there?")
    assert (receipt.status, receipt.error_kind, receipt.route) == ("error", "quota_exhausted", "none")
    assert receipt.answer.startswith("Gemini API error 429") and len(llm.requests) == 1
    assert (receipt.model.api_requests, receipt.model.model) == (1, "fake-model")


def test_sql_step_failure_keeps_the_routing_usage(make):
    agent, _ = make(route("sql"), LLMError("unavailable", "Gemini API error 503 UNAVAILABLE: high demand", attempts=2))
    receipt = agent.ask("How many portfolios are there?")
    assert (receipt.status, receipt.error_kind) == ("error", "unavailable")
    assert (receipt.model.api_requests, receipt.model.prompt_tokens) == (3, 100)


def test_missing_key_is_reported_without_crashing(readonly_db, context):
    agent = PortfolioAgent(readonly_db, context, None, unavailable_reason="Gemini is not configured: the "
                           "GEMINI_API_KEY environment variable is not set.")
    receipt = agent.ask("How many portfolios are there?")
    assert (receipt.status, receipt.error_kind) == ("error", "not_configured") and "GEMINI_API_KEY" in receipt.answer


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001, "abc\x00def"])
def test_invalid_input_never_reaches_gemini(make, question):
    agent, llm = make()
    receipt = agent.ask(question)
    assert receipt.status == "invalid_input" and llm.requests == []


# --- the receipt ------------------------------------------------------------------------------

def test_receipt_round_trips_as_json_and_each_has_its_own_id(make):
    agent, _ = make(route("exposure", portfolio="Balanced Portfolio"), route("exposure", portfolio="Balanced Portfolio"))
    first = agent.ask("Sector exposure of the Balanced Portfolio")
    second = agent.ask("Sector exposure of the Balanced Portfolio")
    assert FinancialAnswerReceipt.model_validate_json(first.model_dump_json()) == first
    assert first.receipt_id != second.receipt_id and first.created_utc.endswith("+00:00")
