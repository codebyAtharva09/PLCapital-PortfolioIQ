"""Milestone 4: the CLI ask command renders the receipt, can save it as JSON and uses distinct exit codes."""

import json

import pytest

from portfolioiq import cli
from portfolioiq.agent import PortfolioAgent
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.receipt import FinancialAnswerReceipt
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_tool import SqlDraft


class ScriptedLLM:
    model = "fake-model"

    def __init__(self, items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(value=item, call=LLMCall(model="fake-model", sdk_version="test", latency_ms=5.0, attempts=1))


def route(action, **fields):
    values = dict(portfolio="", sql_question="", sector="", clarification_question="", options=[],
                  decline_kind="none", reason="Scripted for the test.")
    values.update(fields)
    return RouterDecision(action=action, **values)


def draft(sql, explanation="Scripted query."):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation=explanation)


@pytest.fixture
def use(monkeypatch, readonly_db):
    context = SemanticContext.from_db(readonly_db)

    def install(*items):
        monkeypatch.setattr(cli, "build_agent", lambda: PortfolioAgent(readonly_db, context, ScriptedLLM(items),
                                                                       fingerprint="b" * 64, thinking_level="LOW"))
    return install


def test_ask_shows_the_answer_its_evidence_and_the_receipt(use, capsys):
    use(route("sql"), draft("SELECT COUNT(*) AS portfolio_count FROM portfolios", "Counts every portfolio — all 13."))
    code = cli.main(["ask", "How many portfolios do we have?"])
    out = capsys.readouterr().out
    assert code == 0 and out.isascii()  # model text is made ASCII-safe
    for expected in ["Route: SQL query (router: Scripted for the test.)", "Status: ANSWERED",
                     "Answer: portfolio_count: 13.", "Query (as described by Gemini): Counts every portfolio - all 13.",
                     "SELECT COUNT(*) AS portfolio_count FROM portfolios", "Verification: RESULT_VALIDATED",
                     "Google Gemini API: model fake-model", "API requests 2, cache hits 0", "Answer basis:"]:
        assert expected in out, expected


def test_ask_exposure_shows_both_methods(use, capsys):
    use(route("exposure", portfolio="Tech Innovation Fund"))
    code = cli.main(["ask", "What are the sector exposures for the Tech Innovation Fund?"])
    out = capsys.readouterr().out
    assert code == 0 and "Method A: share of total portfolio" in out and "Method B: equity-sleeve composition" in out
    assert "Verification: INDEPENDENTLY_RECONCILED" in out and "Technology" in out


def test_ask_hybrid_shows_the_portfolio_selection_and_the_ranking(use, capsys):
    use(route("exposure_for_sql_results", sql_question="List the names of high-risk portfolios", sector="Technology"),
        draft("SELECT portfolio_name FROM portfolios WHERE target_risk_level = 'High'"))
    code = cli.main(["ask", "Which high-risk portfolio has the largest Technology exposure?"])
    out = capsys.readouterr().out
    assert code == 0 and "Portfolio selection (asked of the SQL tool)" in out
    assert "Technology exposure by portfolio" in out and "Tech Innovation Fund" in out


def test_ask_clarification_lists_the_choices(use, capsys):
    use(route("exposure", portfolio="international portfolio"))
    code = cli.main(["ask", "Sector exposure for the international portfolio"])
    out = capsys.readouterr().out
    assert code == 2 and "1. International Equity Fund" in out and "2. Total International Index Fund" in out


def test_ask_json_prints_the_receipt_and_save_writes_it(use, capsys, tmp_path):
    use(route("exposure", portfolio="Balanced Portfolio"))
    target = tmp_path / "receipts" / "balanced.json"
    code = cli.main(["ask", "Sector exposure of the Balanced Portfolio", "--json", "--save", str(target)])
    printed = FinancialAnswerReceipt.model_validate(json.loads(capsys.readouterr().out))
    saved = FinancialAnswerReceipt.model_validate_json(target.read_text(encoding="utf-8"))
    assert code == 0 and printed.receipt_id == saved.receipt_id and saved.status == "answered"


@pytest.mark.parametrize("items, code", [
    ((route("clarify", clarification_question="Which measure?", options=["AUM", "1-year return"]),), 2),
    ((route("decline", decline_kind="out_of_scope"),), 2),
    ((route("exposure", portfolio="Bitcoin Fund"),), 2),
    ((route("decline", decline_kind="modifies_data"),), 3),
    ((route("exposure", portfolio="Growth Equity Fund'; DROP TABLE holdings;--"),), 3),
    ((LLMError("quota_exhausted", "Gemini API error 429 RESOURCE_EXHAUSTED: daily quota", attempts=1),), 4),
    ((route("sql"), draft("SELECT nope FROM portfolios"), draft("SELECT still_nope FROM portfolios")), 5),
])
def test_ask_exit_codes(use, capsys, items, code):
    use(*items)
    assert cli.main(["ask", "Some question"]) == code
    assert "Status:" in capsys.readouterr().out


def test_ask_rejects_blank_input_without_calling_gemini(use, capsys):
    use()  # no scripted response: any Gemini request would fail the test
    assert cli.main(["ask", "   "]) == 3
    assert "Status: INVALID INPUT" in capsys.readouterr().out
