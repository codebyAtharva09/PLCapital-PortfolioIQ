"""Agent runs: receipts recorded as they arrive, budget rules applied, scored offline (fake LLM, no network)."""

import dataclasses
import json

import pytest

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import load_settings
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_tool import SqlDraft
from portfolioiq_eval import agent_eval

CASES = agent_eval.load_cases()
QUOTA = LLMError("quota_exhausted", "Gemini API error 429 RESOURCE_EXHAUSTED: daily quota", 54000.0, attempts=1)
OUTAGE = LLMError("unavailable", "Gemini API error 503 UNAVAILABLE: high demand", attempts=2)  # a 503 and its retry
ONE_OUTAGE = LLMError("unavailable", "Gemini API error 503 UNAVAILABLE: high demand", attempts=1)
BUDGET = LLMError("budget_exhausted", "The request budget is used up: 5 of 5 API requests sent.", attempts=0)


class ScriptedLLM:
    model = "fake-model"

    def __init__(self, items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(value=item, call=LLMCall(model="fake-model", sdk_version="test", latency_ms=10.0, attempts=1,
                                                  prompt_tokens=100, output_tokens=10, thinking_tokens=1,
                                                  request_hash="cd" * 32))


def route(action, **fields):
    values = dict(portfolio="", sql_question="", sector="", clarification_question="", options=[],
                  decline_kind="none", reason="Scripted for the test.")
    values.update(fields)
    return RouterDecision(action=action, **values)


def draft(sql):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation="Scripted query.")


WELL_BEHAVED = [
    route("sql"),
    draft("SELECT COUNT(*) FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id "
          "WHERE p.portfolio_name = 'Dividend Aristocrats Fund'"),
    route("exposure", portfolio="Tech Innovation Fund"),
    route("exposure", portfolio="international portfolio"),
    route("exposure_for_sql_results", sql_question="List the names of high-risk portfolios", sector="Technology"),
    draft("SELECT portfolio_name FROM portfolios WHERE target_risk_level = 'High'"),
]


@pytest.fixture
def settings(readonly_db, tmp_path):
    return dataclasses.replace(load_settings(), cache_dir=readonly_db.parent, api_usage_log=tmp_path / "usage.jsonl",
                               llm_cache_dir=None)


@pytest.fixture
def agent(readonly_db):
    context = SemanticContext.from_db(readonly_db)
    return lambda *items: PortfolioAgent(readonly_db, context, ScriptedLLM(items), fingerprint="c" * 64)


def meta_of(outputs):
    return json.loads((outputs.parent / "run_meta.json").read_text(encoding="utf-8"))


def test_integration_questions_fix_their_expected_behaviour_in_advance():
    assert [c.id for c in CASES] == ["A1", "A2", "A3", "A4"]
    assert {c.expect["route"] for c in CASES} == {"sql", "exposure", "exposure_for_sql_results"}
    assert all("status" in c.expect for c in CASES)


def test_live_run_records_receipts_and_scores_them_offline(agent, settings, tmp_path):
    outputs = agent_eval.run(settings, CASES, 0.0, runs_dir=tmp_path / "runs", agent=agent(*WELL_BEHAVED))
    report = agent_eval.score([outputs], CASES)
    assert (report["summary"]["as_expected"], report["summary"]["api_requests"]) == (4, 6)
    assert report["summary"]["verdicts"] == {"PASS": 4, "FAIL": 0, "REFUSED": 0, "NOT EVALUATED": 0}
    meta = meta_of(outputs)
    assert meta["attempted"] == ["A1", "A2", "A3", "A4"] and meta["db_sha256_before"] == meta["db_sha256_after"]
    assert meta["sql_prompt_version"] and meta["router_version"]
    a4 = report["items"][3]
    assert (a4["api_requests"], a4["prompt_tokens"], a4["verification"]) == (2, 200, "RESULT_VALIDATED")
    assert a4["raw_response"] == ("reports/api_usage.jsonl: step 1 router cdcdcdcdcdcdcdcd (api), "
                                  "step 2 sql_query cdcdcdcdcdcdcdcd (api)")
    assert a4["sql_prompt_version"] == meta["sql_prompt_version"] and a4["explanation"].startswith("Behaved as expected")
    _, md_path = agent_eval.write_reports(report, tmp_path / "reports")
    markdown = md_path.read_text(encoding="utf-8")
    assert "| A4 |" in markdown and "## Provenance" in markdown and "| PASS |" in markdown


def test_unexpected_behaviour_is_reported_with_its_differences(agent, settings, tmp_path):
    a2 = CASES[1]
    misrouted = agent(route("sql"), draft("SELECT s.symbol FROM holdings h JOIN securities s "
                                          "ON s.security_id = h.security_id WHERE h.portfolio_id = 3"))
    outputs = agent_eval.run(settings, [a2], 0.0, runs_dir=tmp_path / "runs", agent=misrouted)
    (item,) = agent_eval.score([outputs], [a2])["items"]
    assert (item["outcome"], item["verdict"]) == ("unexpected", "FAIL")
    assert item["problems"] == ["route sql, expected exposure",
                                "exposure for no portfolio, expected Tech Innovation Fund"]


def test_run_stops_on_an_exhausted_quota_and_failures_are_not_evaluated(agent, settings, tmp_path):
    outputs = agent_eval.run(settings, CASES, 0.0, runs_dir=tmp_path / "runs", agent=agent(QUOTA))
    meta = meta_of(outputs)
    assert meta["attempted"] == ["A1"] and meta["stopped"]["kind"] == "quota_exhausted"
    items = agent_eval.score([outputs], CASES)["items"]
    assert [i["verdict"] for i in items] == ["NOT EVALUATED"] * 4
    assert items[0]["api_error"] == "quota_exhausted" and items[0]["api_requests"] == 1
    assert agent_eval.answered_case_ids([outputs]) == set()


def test_run_stops_after_two_consecutive_outage_responses(agent, settings, tmp_path):
    meta = meta_of(agent_eval.run(settings, CASES, 0.0, runs_dir=tmp_path / "runs", agent=agent(OUTAGE)))
    assert meta["attempted"] == ["A1"] and meta["stopped"]["kind"] == "2 consecutive unavailable errors"
    meta = meta_of(agent_eval.run(settings, CASES, 0.0, runs_dir=tmp_path / "runs", agent=agent(ONE_OUTAGE, ONE_OUTAGE)))
    assert meta["attempted"] == ["A1", "A2"] and meta["stopped"]["kind"] == "2 consecutive unavailable errors"
    answered_between = agent(ONE_OUTAGE, route("exposure", portfolio="Tech Innovation Fund"), ONE_OUTAGE, *WELL_BEHAVED[4:])
    meta = meta_of(agent_eval.run(settings, CASES, 0.0, runs_dir=tmp_path / "runs", agent=answered_between))
    assert meta["attempted"] == ["A1", "A2", "A3", "A4"] and meta["stopped"] is None


def test_run_stops_when_the_request_budget_is_used_up(agent, settings, tmp_path):
    outputs = agent_eval.run(settings, CASES, 0.0, runs_dir=tmp_path / "runs", agent=agent(route("sql"), BUDGET))
    assert meta_of(outputs)["stopped"]["kind"] == "budget_exhausted"
    (item, *rest) = agent_eval.score([outputs], CASES)["items"]
    assert (item["verdict"], item["api_error"], item["api_requests"]) == ("NOT EVALUATED", "budget_exhausted", 1)
    assert [i["verdict"] for i in rest] == ["NOT EVALUATED"] * 3


def test_an_api_failure_never_replaces_a_valid_receipt(agent, settings, tmp_path):
    a2, runs = CASES[1], tmp_path / "runs"
    first = agent_eval.run(settings, [a2], 0.0, runs_dir=runs, agent=agent(route("exposure", portfolio="Tech Innovation Fund")))
    second = agent_eval.run(settings, [a2], 0.0, runs_dir=runs, agent=agent(QUOTA))
    assert first.parent != second.parent
    (item,) = agent_eval.score(agent_eval.recorded_outputs(runs), [a2])["items"]
    assert (item["outcome"], item["run"]) == ("as_expected", first.parent.name)
    assert agent_eval.answered_case_ids([first, second]) == {"A2"}
