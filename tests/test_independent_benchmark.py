"""The independent benchmark: a frozen, independently verified dataset and a scorer that discriminates.

Nothing here talks to Gemini. A scripted stand-in plays the model. Scripting the *correct* answers proves that the
dataset, the oracles, the reference SQL, the agent and the scorer agree with one another; it says nothing about how
well Gemini does. Scripting wrong answers proves the scorer notices.
"""

import dataclasses
import difflib
import json
from collections import Counter

import pytest

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import PROJECT_ROOT, load_settings
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.router import ROUTING_EXAMPLES, RouterDecision
from portfolioiq.semantic import VERIFIED_EXAMPLES, SemanticContext
from portfolioiq.sql_engine import execute_sql
from portfolioiq.sql_guard import validate_sql
from portfolioiq.sql_tool import PROMPT_VARIANTS, SqlDraft
from portfolioiq_eval import benchmark_oracles, independent
from portfolioiq_eval.__main__ import main
from portfolioiq_eval.comparator import compare_results, requires_order

ALL = independent.load_cases(("dev", "validation", "test"))
BY_ID = {c["id"]: c for c in ALL}
CATEGORIES = {"Basic portfolio lookups", "Security and sector lookups", "Aggregations and totals", "Joins across tables",
              "Filtering and ranking", "Date-based questions", "Financial metrics", "Sector exposure",
              "Hybrid SQL plus exposure", "Ambiguous portfolio references", "Empty and missing data",
              "Invalid requests", "Adversarial inputs"}


# --- the dataset ------------------------------------------------------------------------------------------------

def test_dataset_shape_and_coverage():
    assert len(ALL) == 51 and len({c["id"] for c in ALL}) == 51
    assert Counter(c["split"] for c in ALL) == {"dev": 22, "validation": 13, "test": 16}
    assert {c["category"] for c in ALL} == CATEGORIES
    assert all(c["id"][0] == c["split"][0].upper() for c in ALL)  # D, V, T prefixes match the split
    assert all(sum(1 for c in ALL if c["category"] == cat) >= 3 for cat in CATEGORIES)
    assert {c["kind"] for c in ALL} == set(independent.REQUESTS)


def test_dataset_is_frozen_and_edits_are_detected(tmp_path, monkeypatch):
    independent.verify_frozen()  # the committed files match their recorded checksums
    edited = tmp_path / "edited.json"
    edited.write_text(independent.DEV_VALIDATION.read_text(encoding="utf-8").replace("Balanced", "Edited"), encoding="utf-8")
    monkeypatch.setattr(independent, "DEV_VALIDATION", edited)
    with pytest.raises(RuntimeError, match="frozen checksum"):
        independent.verify_frozen()


def test_held_out_cases_are_loaded_only_when_requested():
    assert not [c for c in independent.load_cases(("dev", "validation")) if c["split"] == "test"]
    assert {c["split"] for c in independent.load_cases(("test",))} == {"test"}
    with pytest.raises(ValueError):
        independent.load_cases(("final",))


@pytest.mark.parametrize("case", [c for c in ALL if "reference_sql" in c], ids=lambda c: c["id"])
def test_reference_sql_agrees_with_the_independent_oracle_and_the_stored_value(case, readonly_db, schema):
    expected = case["expected"]
    assert validate_sql(case["reference_sql"], schema).ok
    result = execute_sql(readonly_db, case["reference_sql"])
    assert result.status == "ok"
    assert compare_results(expected["columns"], expected["rows"], result.columns, result.rows,
                           ordered=case.get("ordered", False)).strict
    if case["kind"] == "sql":
        assert case["ordered"] == requires_order(case["reference_sql"])
    # three-way: the stored value, the oracle computed now from the CSV files, and the SQL result all agree
    oracle = benchmark_oracles.ORACLES[case["id"]](benchmark_oracles.Tables())
    assert json.loads(json.dumps(oracle)) == json.loads(json.dumps(expected))


@pytest.mark.parametrize("case", [c for c in ALL if c["kind"] in ("exposure", "hybrid", "clarify")], ids=lambda c: c["id"])
def test_stored_exposure_hybrid_and_clarification_values_match_the_oracle(case, readonly_db):
    oracle = benchmark_oracles.ORACLES[case["id"]](benchmark_oracles.Tables())
    assert json.loads(json.dumps(oracle)) == json.loads(json.dumps(case["expected"]))
    if case["kind"] == "hybrid":
        names = sorted(row[0] for row in execute_sql(readonly_db, case["selection_sql"]).rows)
        assert names == case["expected"]["portfolios"]
        assert len(case["expected"]["leader"]) > 0


def test_questions_are_not_in_any_prompt_and_not_copies_of_evaluation_questions():
    resident = [e.question for e in VERIFIED_EXAMPLES] + [q for q, _ in ROUTING_EXAMPLES]
    seen = [q["question"] for q in json.loads((PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json").read_text("utf-8"))["questions"]]
    unseen = json.loads((PROJECT_ROOT / "evaluation" / "unseen_sql_questions.json").read_text("utf-8"))
    seen += [q["question"] for q in unseen["questions"] + unseen["safety"]]
    seen += [s["scenario"] for s in json.loads((PROJECT_ROOT / "evaluation" / "reliability_scenarios.json").read_text("utf-8"))["scenarios"]]
    texts = [c["question"].lower().strip() for c in ALL if c["question"].strip()]
    for text in texts:
        for other in resident + seen:
            ratio = difflib.SequenceMatcher(None, text, other.lower()).ratio()
            assert ratio < 0.85, (text, other, round(ratio, 2))
        for other in resident:
            assert difflib.SequenceMatcher(None, text, other.lower()).ratio() < 0.8
    # nothing from the benchmark (question, reference SQL or expected value) appears inside a runtime prompt
    prompt_text = "\n".join(v.instruction for v in PROMPT_VARIANTS.values())
    for case in ALL:
        assert case["question"].strip() == "" or case["question"] not in prompt_text
        for key in ("reference_sql", "selection_sql", "attack_sql"):
            assert not case.get(key) or case[key] not in prompt_text


# --- a scripted stand-in for the model ----------------------------------------------------------------------------

class ScriptedLLM:
    model = "fake-model"

    def __init__(self, items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(value=item, call=LLMCall(model="fake-model", sdk_version="t", latency_ms=1.0, attempts=1,
                                                  prompt_tokens=10, output_tokens=2, thinking_tokens=0,
                                                  request_hash="ab" * 32))


def route(action, **fields):
    values = dict(portfolio="", sql_question="", sector="", clarification_question="", options=[],
                  decline_kind="none", reason="scripted")
    return RouterDecision(action=action, **{**values, **fields})


def draft(sql):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation="scripted query")


def correct_script(case):
    """What a perfect model would return for this case, call by call."""
    kind = case["kind"]
    if kind in ("sql", "no_data"):
        return [route("sql"), draft(case["reference_sql"])]
    if kind in ("exposure", "clarify", "not_found"):
        return [route("exposure", portfolio=case.get("phrase", ""))]
    if kind == "hybrid":
        return [route("exposure_for_sql_results", sql_question=case["sql_question"], sector=case["sector"]),
                draft(case["selection_sql"])]
    if kind == "decline":
        return [route("decline", decline_kind="out_of_scope")]
    if kind == "refuse":
        if case.get("attack_sql"):
            return [route("sql"), draft(case["attack_sql"])]  # the guard, not the model, must stop it
        return [route("decline", decline_kind="out_of_scope")]
    return []  # invalid input never reaches the model


@pytest.fixture
def settings(readonly_db, tmp_path):
    return dataclasses.replace(load_settings(), cache_dir=readonly_db.parent, api_usage_log=tmp_path / "usage.jsonl",
                               llm_cache_dir=None)


def play(settings, readonly_db, tmp_path, cases, scripts):
    """Run ``cases`` through the real agent with scripted model output, record, and score."""
    items = [item for case in cases for item in scripts[case["id"]]]
    agent = PortfolioAgent(readonly_db, SemanticContext.from_db(readonly_db), ScriptedLLM(items))
    outputs = independent.run(settings, cases, 0.0, runs_dir=tmp_path / "runs", agent=agent, tag="test")
    assert agent.llm.items == []  # every scripted answer was used, in order
    return independent.score([outputs], cases)


def test_a_perfect_model_scores_every_case_and_every_track(settings, readonly_db, tmp_path):
    report = play(settings, readonly_db, tmp_path, ALL, {c["id"]: correct_script(c) for c in ALL})
    failed = [(i["id"], i["explanation"]) for i in report["items"] if i["verdict"] not in ("PASS", "REFUSED")]
    assert not failed, failed
    s = report["summary"]
    # 4 adversarial + 1 out-of-scope + 2 invalid-input cases are expected refusals; the other 44 are answers
    assert s["verdicts"] == {"PASS": 44, "FAIL": 0, "REFUSED": 7, "NOT EVALUATED": 0}
    assert s["silent_failures"] == 0 and s["not_evaluated"] == 0
    for name, track in s["tracks"].items():
        assert track["correct"] == track["evaluated"] > 0, name
    assert s["tracks"]["sql_strict_columns"]["evaluated"] == 29  # the 29 SQL cases, all exact-column too
    # even a perfect 51 of 51 only supports a lower bound of about 93%: the sample is small
    assert 0.92 < s["tracks"]["end_to_end"]["ci95"][0] < 0.94
    # the clarification cases were answered by the resolver with exactly the expected candidates
    strict = {i["id"]: i["strict"] for i in report["items"] if i["kind"] == "clarify"}
    assert strict == {"D19": True, "V10": True, "T11": True}
    json_path, md_path = independent.write_reports(report, tmp_path / "reports")
    text = md_path.read_text(encoding="utf-8")
    assert "Wilson" in text and "| D01 |" in text and json.loads(json_path.read_text())["summary"]["cases"] == 51


def test_wrong_answers_are_caught_and_silent_failures_are_counted(settings, readonly_db, tmp_path):
    cases = [BY_ID[i] for i in ("D04", "D07", "D01", "D15", "V09")]
    scripts = {
        "D04": [route("sql"), draft("SELECT symbol FROM securities WHERE asset_type = 'Stock'")],  # wrong rows
        "D07": [route("sql"), draft("SELECT s.symbol, s.company_name FROM holdings h JOIN securities s "
                                    "ON s.security_id = h.security_id JOIN portfolios p ON p.portfolio_id = h.portfolio_id "
                                    "WHERE p.portfolio_name = 'Dividend Aristocrats Fund'")],  # extra column
        "D01": [route("exposure", portfolio="Growth Equity Fund")],  # the wrong tool
        "D15": [route("exposure", portfolio="Balanced Portfolio")],  # the wrong portfolio
        "V09": [route("exposure_for_sql_results", sql_question="List the names of all portfolios", sector="Financials"),
                draft("SELECT portfolio_name FROM portfolios")],  # the wrong selection
    }
    report = play(settings, readonly_db, tmp_path, cases, scripts)
    verdicts = {i["id"]: i["verdict"] for i in report["items"]}
    assert verdicts == {"D04": "FAIL", "D07": "PASS", "D01": "FAIL", "D15": "FAIL", "V09": "FAIL"}
    items = {i["id"]: i for i in report["items"]}
    assert items["D07"]["strict"] is False and items["D07"]["relaxed"] is True  # extras allowed, but reported
    assert items["D04"]["silent_failure"] is True and items["D04"]["verification"] == "RESULT_VALIDATED"
    assert items["D01"]["route_ok"] is False
    assert report["summary"]["silent_failures"] >= 1
    assert report["summary"]["tracks"]["sql_strict_columns"]["correct"] == 0


def test_api_failures_are_not_evaluated_and_never_wrong(settings, readonly_db, tmp_path):
    cases = [BY_ID["D01"], BY_ID["D02"]]
    # one failed request: the run carries on. (Two in a row would, correctly, stop the whole run.)
    outage = LLMError("unavailable", "Gemini API error 503 UNAVAILABLE: high demand", attempts=1)
    scripts = {"D01": [outage], "D02": [route("sql"), draft(BY_ID["D02"]["reference_sql"])]}
    report = play(settings, readonly_db, tmp_path, cases, scripts)
    verdicts = {i["id"]: i["verdict"] for i in report["items"]}
    assert verdicts == {"D01": "NOT EVALUATED", "D02": "PASS"}
    assert report["summary"]["tracks"]["end_to_end"]["evaluated"] == 1
    unrun = independent.score([], cases)
    assert {i["verdict"] for i in unrun["items"]} == {"NOT EVALUATED"} and unrun["summary"]["evaluated"] == 0


def test_refusals_must_really_refuse(settings, readonly_db, tmp_path):
    cases = [BY_ID["T14"], BY_ID["T15"], BY_ID["V12"]]
    scripts = {
        "T14": [route("sql"), draft("SELECT portfolio_name FROM portfolios")],  # answered instead of refusing
        "T15": [route("decline", decline_kind="out_of_scope")],
        "V12": [],
    }
    report = play(settings, readonly_db, tmp_path, cases, scripts)
    verdicts = {i["id"]: i["verdict"] for i in report["items"]}
    assert verdicts == {"T14": "FAIL", "T15": "REFUSED", "V12": "REFUSED"}


# --- confidence intervals, budget and the command ------------------------------------------------------------------

def test_wilson_interval_matches_known_values():
    low, high = independent.wilson(38, 40)
    assert (round(low, 3), round(high, 3)) == (0.835, 0.986)
    assert independent.wilson(0, 0) is None
    low, high = independent.wilson(10, 10)
    assert high == pytest.approx(1.0) and 0.72 < low < 0.73
    assert independent.wilson(8, 10)[0] < 0.8 < independent.wilson(8, 10)[1]


def test_request_budget_matches_the_documented_numbers():
    budget = independent.request_budget(ALL)
    assert budget["cases"] == 51 and budget["by_kind"]["invalid"] == 2
    assert (budget["minimum"], budget["maximum"]) == (sum(independent.REQUESTS[c["kind"]][0] for c in ALL),
                                                      sum(independent.REQUESTS[c["kind"]][1] for c in ALL))
    # 29 SQL cases x 2 + no-data 2 + 5 exposure + 5 hybrid x 2 + 3 clarify + 1 not-found + 1 decline + 4 refuse + 0 invalid
    assert budget["minimum"] == 84 and budget["maximum"] == 246
    assert budget["minimum"] / 20 > 4  # more than four free-tier daily windows for gemini-3.6-flash


def test_the_live_command_passes_the_selected_cases_and_their_limits_to_the_runner(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(independent, "run", lambda settings, cases, interval, **kw: calls.append(([c["id"] for c in cases], kw)))
    assert main(["independent", "--split", "test", "--confirm-heldout", "--only", "T01", "T02", "--max-requests", "4",
                 "--runs-dir", str(tmp_path / "runs"), "--reports-dir", str(tmp_path / "reports")]) == 0
    assert calls[0][0] == ["T01", "T02"] and calls[0][1]["max_requests"] == 4 and calls[0][1]["tag"] == "test"


def test_command_plans_without_requests_and_protects_the_held_out_split(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert main(["independent", "--plan", "--split", "dev", "validation", "test"]) == 0
    assert "51 cases" in capsys.readouterr().out
    assert main(["independent", "--split", "test"]) == 2  # a live run on the held-out set needs --confirm-heldout
    assert "--confirm-heldout" in capsys.readouterr().out
