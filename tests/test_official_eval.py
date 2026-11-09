"""Official evaluation (Q1-Q10 through the agent) and the reliability benchmark: scored offline, fakes only."""

import dataclasses

import pytest

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import load_settings
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_tool import SqlDraft
from portfolioiq_eval import benchmark, official
from portfolioiq_eval.__main__ import main

BUDGET = LLMError("budget_exhausted", "The request budget is used up: 3 of 3 API requests sent.", attempts=0)


class ScriptedLLM:
    model = "fake-model"

    def __init__(self, items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(value=item, call=LLMCall(model="fake-model", sdk_version="test", latency_ms=5.0, attempts=1,
                                                  request_hash="ef" * 32))


def route(action, **fields):
    values = dict(portfolio="", sql_question="", sector="", clarification_question="", options=[],
                  decline_kind="none", reason="Scripted for the test.")
    values.update(fields)
    return RouterDecision(action=action, **values)


def draft(sql):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation="Scripted query.")


@pytest.fixture
def settings(readonly_db, tmp_path):
    return dataclasses.replace(load_settings(), cache_dir=readonly_db.parent, api_usage_log=tmp_path / "usage.jsonl",
                               llm_cache_dir=None)


CASES = official.load_cases()
BY_ID = {c.id: c for c in CASES}


def test_cases_come_from_the_ground_truth_with_fixtures_for_exposure():
    assert [c.id for c in CASES] == [f"Q{i}" for i in range(1, 11)]
    assert all(c.reference_sql and c.expected_route == "sql" for c in CASES[:8])
    assert [(c.portfolio, c.expected_route, c.fixture.name) for c in CASES[8:]] == [
        ("Tech Innovation Fund", "exposure", "q09_tech_innovation_fund.json"),
        ("International Equity Fund", "exposure", "q10_international_equity_fund.json")]


def test_without_live_runs_the_agent_layer_is_pending_and_never_wrong(settings):
    report = official.score([], CASES, settings, sql_outputs=[])
    assert report["summary"]["agent"]["NOT EVALUATED"] == 10 and report["summary"]["agent"]["FAIL"] == 0
    assert all(i["agent"]["explanation"].startswith("PENDING LIVE API ACCESS") for i in report["items"])
    assert report["summary"]["calculation"] == {"PASS": 2, "FAIL": 0, "REFUSED": 0, "NOT EVALUATED": 0}
    q10 = report["items"][9]["calculation"]
    assert "rule B5.1" in q10["explanation"] and q10["verification"] == "INDEPENDENTLY_RECONCILED"
    assert report["summary"]["sql_tool"]["NOT EVALUATED"] == 8  # no SQL records passed in


def test_recorded_sql_tool_results_are_kept_visible(settings):
    report = official.score([], CASES, settings)  # the committed M3 records
    sql = {i["id"]: i["sql_tool"]["verdict"] for i in report["items"] if i.get("sql_tool")}
    assert sql == {"Q1": "PASS", "Q2": "PASS", "Q3": "PASS", "Q4": "PASS", "Q5": "FAIL", "Q6": "PASS",
                   "Q7": "FAIL", "Q8": "PASS"}


def test_live_agent_results_are_scored_per_layer(readonly_db, settings, tmp_path):
    items = [route("sql"), draft("SELECT COUNT(*) FROM portfolios"),                     # Q1 correct
             route("exposure", portfolio="Tech Innovation Fund"),                         # Q9 correct
             route("exposure", portfolio="Balanced Portfolio"),                           # Q10 wrong portfolio
             BUDGET]                                                                      # Q2: cap reached
    agent = PortfolioAgent(readonly_db, SemanticContext.from_db(readonly_db), ScriptedLLM(items))
    chosen = [BY_ID["Q1"], BY_ID["Q9"], BY_ID["Q10"], BY_ID["Q2"]]
    outputs = official.run(settings, chosen, 0.0, runs_dir=tmp_path / "runs", agent=agent)
    assert outputs.name == "official_outputs.jsonl" and outputs.parent.name.startswith("official-")
    report = official.score([outputs], CASES, settings, sql_outputs=[])
    agent_layer = {i["id"]: i["agent"] for i in report["items"]}
    assert agent_layer["Q1"]["verdict"] == "PASS" and agent_layer["Q9"]["verdict"] == "PASS"
    assert agent_layer["Q10"]["verdict"] == "FAIL" and "expected International Equity Fund" in agent_layer["Q10"]["explanation"]
    assert agent_layer["Q2"]["verdict"] == "NOT EVALUATED" and "budget_exhausted" in agent_layer["Q2"]["explanation"]
    assert report["summary"]["agent"]["routing_correct"] == 3 and report["summary"]["agent"]["routing_evaluated"] == 3
    _, md_path = official.write_reports(report, tmp_path / "reports")
    assert "| Q9 |" in md_path.read_text(encoding="utf-8")


def test_wrong_route_fails(readonly_db, settings, tmp_path):
    agent = PortfolioAgent(readonly_db, SemanticContext.from_db(readonly_db),
                           ScriptedLLM([route("exposure", portfolio="Growth Equity Fund")]))
    outputs = official.run(settings, [BY_ID["Q1"]], 0.0, runs_dir=tmp_path / "runs", agent=agent)
    item = official.score([outputs], CASES, settings, sql_outputs=[])["items"][0]["agent"]
    assert item["verdict"] == "FAIL" and item["explanation"] == "Routed to exposure; expected sql."


def live_record(verdict, run="run-x", api_error=None):
    return {"verdict": verdict, "explanation": f"synthetic {verdict}", "run": run, "api_error": api_error}


def test_benchmark_components_pass_and_never_depend_on_live_results(settings, monkeypatch):
    # The deterministic components must not change with whatever live runs exist in reports/.
    monkeypatch.setattr(benchmark, "_live_results", lambda s: {"agent": {}, "sql": {}})
    report = benchmark.run(settings)
    s = report["summary"]
    assert s["scenarios"] == 19 and s["database_unchanged"]
    assert s["component"]["PASS"] == 17 and s["component"]["FAIL"] == 0 and s["component"]["N/A"] == 2
    items = {i["id"]: i for i in report["items"]}
    assert items["R16"]["component"]["explanation"].startswith("Layer 1, SQL guard: rejected")
    assert "guard bypassed" in items["R17"]["component"]["explanation"]
    assert items["R19"]["component"]["explanation"].startswith("SIMULATED with fake clients")
    assert items["R14"]["live"]["verdict"] == "N/A" and items["R19"]["live"]["verdict"] == "N/A"


def test_live_column_is_never_invented_and_reflects_only_recorded_runs(settings, monkeypatch):
    live = {"agent": {}, "sql": {}}
    monkeypatch.setattr(benchmark, "_live_results", lambda s: live)
    nothing = {i["id"]: i["live"]["verdict"] for i in benchmark.run(settings)["items"]}
    assert {nothing[r] for r in ("R01", "R05", "R09", "R10", "R16", "R17")} == {"NOT EVALUATED"}  # nothing recorded
    live["agent"] = {"A4": live_record("PASS"), "A3": live_record("NOT EVALUATED", run=None)}
    live["sql"] = {"S1": live_record("REFUSED"), "S2": live_record("FAIL"), "U1": live_record("PASS")}
    recorded = {i["id"]: i["live"]["verdict"] for i in benchmark.run(settings)["items"]}
    assert recorded["R01"] == "PASS" and recorded["R16"] == "REFUSED" and recorded["R17"] == "FAIL"
    assert recorded["R09"] == "PASS" and recorded["R05"] == "NOT EVALUATED"  # A3 has no valid record in this fixture
    assert recorded["R13"] == "NOT EVALUATED" and recorded["R18"] == "NOT EVALUATED"  # no live source defined


def test_benchmark_and_official_commands_make_no_api_calls(settings, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(benchmark, "REPORTS", tmp_path)
    monkeypatch.setattr(benchmark, "write_reports",
                        lambda report, directory=tmp_path, name="reliability_benchmark": (tmp_path / "b.json",))
    monkeypatch.setattr(official, "write_reports", lambda report, *args: (tmp_path / "o.json",))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert main(["benchmark"]) == 0
    assert "Deterministic components: PASS 17, N/A 2" in capsys.readouterr().out
    assert main(["official", "--rescore"]) == 0
    assert "calculation Q9-Q10: PASS 2" in capsys.readouterr().out


def test_runs_from_different_models_are_never_scored_together(tmp_path, capsys):
    from portfolioiq_eval.__main__ import mixed_models

    def run(name, model):
        directory = tmp_path / "runs" / name
        directory.mkdir(parents=True)
        (directory / "run_meta.json").write_text(f'{{"model": "{model}"}}', encoding="utf-8")
        output = directory / "sql_outputs.jsonl"
        output.write_text("", encoding="utf-8")
        return output

    official_run = run("a", "gemini-3.6-flash")
    assert mixed_models([official_run]) is None
    assert mixed_models([official_run], "gemini-3.6-flash") is None
    assert "more than one model" in mixed_models([official_run], "gemini-3.5-flash-lite")  # before a live run
    other = run("b", "gemini-3.5-flash-lite")
    assert "more than one model" in mixed_models([official_run, other])
    assert main(["sql", "--rescore", str(official_run), str(other), "--reports-dir", str(tmp_path)]) == 2
    assert "Not scored" in capsys.readouterr().out
