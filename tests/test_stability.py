"""Stability benchmark: rounds recorded separately with the cache off, scored by outcome (fakes only)."""

import dataclasses

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import load_settings
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_tool import SqlDraft
from portfolioiq_eval import agent_eval, stability
from portfolioiq_eval.agent_eval import AgentCase


class ScriptedLLM:
    model = "fake-model"

    def __init__(self, items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(value=item, call=LLMCall(model="fake-model", sdk_version="t", latency_ms=1.0, attempts=1))


def route(action, **fields):
    values = dict(portfolio="", sql_question="", sector="", clarification_question="", options=[],
                  decline_kind="none", reason="test")
    return RouterDecision(action=action, **{**values, **fields})


def draft(sql):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation="q")


def test_cases_and_consistency_scoring(readonly_db, tmp_path):
    assert [c.id for c in stability.load_cases()] == ["Q4", "Q7", "Q10", "R01", "R09"]
    settings = dataclasses.replace(load_settings(), cache_dir=readonly_db.parent, api_usage_log=None)
    context = SemanticContext.from_db(readonly_db)
    cases = [AgentCase("Q4", "What is the total AUM for high-risk portfolios?", {}),
             AgentCase("Q10", "Calculate the sector exposure breakdown for international equity", {})]
    same = "SELECT SUM(total_aum) FROM portfolios WHERE target_risk_level = 'High'"
    rounds = [
        [route("sql"), draft(same), route("exposure", portfolio="international equity")],
        [route("sql"), draft(same), route("exposure", portfolio="international equity")],
        [route("sql"), draft("SELECT SUM(total_aum) FROM portfolios"),
         LLMError("unavailable", "503", attempts=2)],
    ]
    outputs = []
    for items in rounds:
        agent = PortfolioAgent(readonly_db, context, ScriptedLLM(items))
        outputs.append(agent_eval.run(settings, cases, 0.0, runs_dir=tmp_path, agent=agent, prefix="stability-",
                                      outputs_name=stability.OUTPUTS_NAME))
    assert stability.recorded_outputs(tmp_path) == sorted(outputs)
    report = stability.score(outputs, cases)
    items = {i["id"]: i for i in report["items"]}
    assert items["Q4"]["verdict"] == "FAIL" and items["Q4"]["consistency"] == round(2 / 3, 3)
    assert items["Q10"]["verdict"] == "PASS" and items["Q10"]["api_failures"] == 1  # the failed round is excluded
    assert items["Q10"]["evaluated"] == 2
    _, md = stability.write_reports(report, tmp_path / "reports")
    assert "| Q4 |" in md.read_text(encoding="utf-8")


def test_rounds_run_with_the_response_cache_off(monkeypatch, tmp_path):
    seen = {}

    def fake_run(settings, cases, interval, **kwargs):
        seen["cache"], seen["prefix"] = settings.llm_cache_dir, kwargs["prefix"]
        return tmp_path / "x"

    monkeypatch.setattr(agent_eval, "run", fake_run)
    stability.run_round(load_settings(), [], 0.0, runs_dir=tmp_path, max_requests=5)
    assert seen == {"cache": None, "prefix": "stability-"}
