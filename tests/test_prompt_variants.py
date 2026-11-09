"""The production baseline is frozen and recoverable; a candidate configuration is separate, versioned and never mixed with it."""

import difflib
import importlib.util
import json
import re
import sys

import pytest

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import PROJECT_ROOT, load_settings
from portfolioiq.llm import LLMCall, LLMResult, request_key
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import CANDIDATE_EXAMPLES, VERIFIED_EXAMPLES, SemanticContext
from portfolioiq.sql_tool import (BASELINE_VARIANT, CANDIDATE_22_VARIANT, CANDIDATE_INSTRUCTION, CANDIDATE_VARIANT, PROMPT_VARIANTS,
                                  SYSTEM_INSTRUCTION, SqlDraft, SqlTool, instruction_for, sql_prompt_version, variant_for)
from portfolioiq_eval import independent
from portfolioiq_eval.__main__ import live_prompt_version, mixed_models

SNAPSHOT = json.loads((PROJECT_ROOT / "evaluation" / "baseline_configuration.json").read_text(encoding="utf-8"))
spec = importlib.util.spec_from_file_location("snapshot_baseline", PROJECT_ROOT / "scripts" / "snapshot_baseline.py")
snapshot_tool = importlib.util.module_from_spec(spec)
sys.modules["snapshot_baseline"] = snapshot_tool
spec.loader.exec_module(snapshot_tool)


# --- the baseline -----------------------------------------------------------------------------------------------

def test_baseline_versions_are_the_ones_every_official_run_recorded():
    assert SNAPSHOT["versions"] == {"router": "676786d09950", "sql_prompt": "d5a914eb71d5", "context": "4b74dcd0419a"}
    runs = sorted((PROJECT_ROOT / "reports" / "runs").glob("*/run_meta.json"))
    recorded = {json.loads(p.read_text(encoding="utf-8")).get("sql_prompt_version") or
                json.loads(p.read_text(encoding="utf-8")).get("prompt_version") for p in runs}
    assert recorded - {None} == {"d5a914eb71d5"}  # no official run ever used another prompt version


def test_current_code_still_produces_the_frozen_baseline():
    current = snapshot_tool.current()
    manifest = json.loads((PROJECT_ROOT / "evaluation" / "security_manifest.json").read_text(encoding="utf-8"))
    drift = snapshot_tool.drift(current, SNAPSHOT, manifest)
    assert not drift, f"the frozen baseline drifted in: {drift}. A candidate must not change these."


def test_baseline_instruction_can_be_restored_byte_for_byte():
    assert PROMPT_VARIANTS[BASELINE_VARIANT].instruction == SYSTEM_INSTRUCTION == SNAPSHOT["sql_instruction_text"]
    assert snapshot_tool.sha256_text(SYSTEM_INSTRUCTION) == SNAPSHOT["sql_instruction_sha256"]
    assert sql_prompt_version("any context") == sql_prompt_version("any context", SYSTEM_INSTRUCTION)  # the default


def test_the_baseline_runs_no_shape_policy_and_keeps_its_examples():
    baseline = variant_for(BASELINE_VARIANT)
    assert baseline.shape_policy is None and baseline.examples == VERIFIED_EXAMPLES


def test_the_snapshot_is_never_overwritten(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(snapshot_tool, "SNAPSHOT", tmp_path / "baseline.json")
    monkeypatch.setattr(sys, "argv", ["snapshot_baseline.py"])
    assert snapshot_tool.main() == 0 and snapshot_tool.main() == 2
    assert "never overwritten" in capsys.readouterr().out


# --- the candidate ------------------------------------------------------------------------------------------------

def test_candidate_has_its_own_versions_and_leaves_the_baseline_alone(readonly_db):
    baseline_context = SemanticContext.from_db(readonly_db)
    candidate_context = SemanticContext.from_db(readonly_db, examples=CANDIDATE_EXAMPLES)
    assert sql_prompt_version(baseline_context.text, SYSTEM_INSTRUCTION) == "d5a914eb71d5"
    assert baseline_context.version == "4b74dcd0419a"
    assert candidate_context.version != baseline_context.version
    assert sql_prompt_version(candidate_context.text, CANDIDATE_INSTRUCTION) != "d5a914eb71d5"


def test_v22_is_v21_plus_three_rule_changes_and_v21_keeps_its_recorded_versions(readonly_db):
    v21, v22 = variant_for(CANDIDATE_VARIANT), variant_for(CANDIDATE_22_VARIANT)
    assert sql_prompt_version(SemanticContext.from_db(readonly_db, examples=v21.examples).text, v21.instruction) == "5219dec6dd3e"
    assert (v21.shape_policy, v22.shape_policy) == ("2.1", "2.2")
    for added in ("it is already identified", "asks for the entity: return its identity", "A date used only to pick the latest row"):
        assert added in v22.instruction and added not in v21.instruction
    assert all(line in v22.instruction for line in v21.instruction.splitlines() if not line.startswith(("3.", "A column used")))
    assert [e.id for e, f in zip(v21.examples, v22.examples) if e != f] == ["VE4"]
    version = sql_prompt_version(SemanticContext.from_db(readonly_db, examples=v22.examples).text, v22.instruction)
    assert version not in ("5219dec6dd3e", "d5a914eb71d5")


def test_v23_is_v22_plus_one_rule_and_its_evaluated_version_is_pinned(readonly_db):
    v22, v23 = variant_for("candidate-2.2"), variant_for("candidate-2.3")
    assert sql_prompt_version(SemanticContext.from_db(readonly_db, examples=v23.examples).text, v23.instruction) == "314e91c11435"
    assert "returned with the two values it divides" in v23.instruction and "returned with the two values it divides" not in v22.instruction
    assert v23.examples == v22.examples and v23.shape_policy == "2.3"
    assert load_settings().sql_prompt_variant == "candidate-2.3"  # the promoted production default


def test_candidate_changes_only_what_it_says_it_changes():
    kept = [line for line in SYSTEM_INSTRUCTION.splitlines() if "identifying names" not in line
            and "company_name) plus" not in line and "Do not round" not in line]
    assert all(line in CANDIDATE_INSTRUCTION for line in kept)
    assert "Do not round values unless the question asks for rounding or the value is a percentage" in CANDIDATE_INSTRUCTION


def test_unknown_variants_are_errors_never_silent_defaults(monkeypatch):
    with pytest.raises(ValueError, match="candidate-9"):
        instruction_for("candidate-9")
    assert load_settings().sql_prompt_variant == "candidate-2.3"  # production default
    monkeypatch.setenv("PORTFOLIOIQ_SQL_PROMPT", BASELINE_VARIANT)
    assert load_settings().sql_prompt_variant == BASELINE_VARIANT  # the rollback
    monkeypatch.setenv("PORTFOLIOIQ_SQL_PROMPT", CANDIDATE_VARIANT)
    assert load_settings().sql_prompt_variant == CANDIDATE_VARIANT
    monkeypatch.setenv("PORTFOLIOIQ_SQL_PROMPT", "typo")
    with pytest.raises(ValueError):
        SqlTool.from_settings(load_settings())


def _evaluation_questions() -> list[str]:
    evaluation = PROJECT_ROOT / "evaluation"
    questions = [q["question"] for q in json.loads((evaluation / "ground_truth_dataset.json").read_text("utf-8"))["questions"]]
    unseen = json.loads((evaluation / "unseen_sql_questions.json").read_text("utf-8"))
    questions += [q["question"] for q in unseen["questions"] + unseen["safety"]]
    questions += [q["question"] for q in json.loads((evaluation / "agent_integration_questions.json").read_text("utf-8"))["questions"]]
    questions += [c["question"] for c in independent.load_cases(("dev", "validation", "test")) if c["question"].strip()]
    return questions


def test_candidate_text_contains_no_evaluation_material():
    text = CANDIDATE_INSTRUCTION
    forbidden = re.compile(r"\bQ\d+\b|\bU\d+\b|benchmark|ground.?truth|expected|reference|official|held.?out", re.I)
    assert not forbidden.search(text), forbidden.search(text)
    assert not [q for q in _evaluation_questions() if q in text]
    assert not re.search(r"Growth Equity|Tech Innovation|Fixed Income Plus|Technology", text)  # no data values


def test_new_candidate_examples_are_not_copies_of_any_evaluation_question():
    changed = [e for e in CANDIDATE_EXAMPLES if e not in VERIFIED_EXAMPLES]
    assert {e.id for e in changed} == {"VE3", "VE6", "VE7"}
    new = [e for e in changed if e.question not in {v.question for v in VERIFIED_EXAMPLES}]  # VE3 keeps its question
    assert {e.id for e in new} == {"VE6", "VE7"}
    for example in new:
        for question in _evaluation_questions():
            ratio = difflib.SequenceMatcher(None, example.question.lower(), question.lower()).ratio()
            assert ratio < 0.7, (example.id, question, round(ratio, 2))
    for example in changed:
        assert "Growth Equity" not in example.sql and "Technology" not in example.sql and "cost_basis" not in example.sql


class Capture:
    model = "fake-model"

    def __init__(self, items):
        self.items, self.systems = list(items), []

    def generate(self, *, system, prompt, schema, purpose=""):
        self.systems.append(system)
        return LLMResult(value=self.items.pop(0), call=LLMCall(model="fake-model", sdk_version="t", latency_ms=1.0,
                                                               attempts=1, request_hash="cd" * 32))


def agent_for(readonly_db, variant):
    llm = Capture([RouterDecision(action="sql", portfolio="", sql_question="", sector="", clarification_question="",
                                  options=[], decline_kind="none", reason="r"),
                   SqlDraft(answerable=True, decline_reason="none", sql="SELECT COUNT(*) FROM portfolios", explanation="q")])
    context = SemanticContext.from_db(readonly_db, examples=variant_for(variant).examples)
    tool = SqlTool(readonly_db, context, llm, variant=variant)
    return PortfolioAgent(readonly_db, context, llm, sql_tool=tool), llm


@pytest.mark.parametrize("variant", [BASELINE_VARIANT, CANDIDATE_VARIANT])
def test_the_chosen_instruction_is_what_is_sent_and_what_the_receipt_records(readonly_db, variant):
    agent, llm = agent_for(readonly_db, variant)
    receipt = agent.ask("How many portfolios are there in the database?")
    assert llm.systems[-1] == PROMPT_VARIANTS[variant].instruction  # the SQL draft request carried exactly this instruction
    assert receipt.data.sql_prompt_variant == variant
    assert receipt.data.sql_prompt_version == agent.sql_tool.prompt_version
    assert receipt.sql.prompt_variant == variant and receipt.sql.prompt_version == agent.sql_tool.prompt_version
    if variant == BASELINE_VARIANT:
        assert receipt.data.sql_prompt_version == "d5a914eb71d5" and receipt.sql.shape_policy is None
    else:
        assert receipt.data.sql_prompt_version != "d5a914eb71d5" and receipt.sql.shape_policy is not None


def test_a_candidate_request_has_a_different_cache_key_so_it_can_never_reuse_baseline_responses():
    key = {name: request_key("gemini-3.6-flash", "LOW", v.instruction, "same prompt", SqlDraft) for name, v in PROMPT_VARIANTS.items()}
    assert key[BASELINE_VARIANT] != key[CANDIDATE_VARIANT]


# --- the evaluator refuses to mix configurations --------------------------------------------------------------------

def meta_file(tmp_path, name, **meta):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    output = folder / "sql_outputs.jsonl"
    output.write_text("", encoding="utf-8")
    return output


def test_runs_with_different_prompt_versions_are_never_scored_together(tmp_path):
    base = meta_file(tmp_path, "a", model="gemini-3.6-flash", prompt_version="d5a914eb71d5")
    agent_base = meta_file(tmp_path, "b", model="gemini-3.6-flash", sql_prompt_version="d5a914eb71d5")
    candidate = meta_file(tmp_path, "c", model="gemini-3.6-flash", prompt_version="aaaaaaaaaaaa")
    old = meta_file(tmp_path, "d", model="gemini-3.6-flash")  # recorded before versions existed: the baseline
    assert mixed_models([base, agent_base, old]) is None
    assert mixed_models([base, agent_base], "gemini-3.6-flash", "d5a914eb71d5") is None
    assert "SQL prompt version" in mixed_models([base, candidate])
    assert "SQL prompt version" in mixed_models([base], "gemini-3.6-flash", "aaaaaaaaaaaa")  # a live candidate run
    assert "model" in mixed_models([base], "gemini-3.5-flash-lite", "d5a914eb71d5")
    assert mixed_models([old], "gemini-3.6-flash", "aaaaaaaaaaaa") is None  # nothing versioned to conflict with yet


def test_the_live_prompt_version_follows_the_chosen_variant(monkeypatch):
    assert live_prompt_version(load_settings()) == "314e91c11435"  # the promoted default
    monkeypatch.setenv("PORTFOLIOIQ_SQL_PROMPT", BASELINE_VARIANT)
    assert live_prompt_version(load_settings()) == "d5a914eb71d5"  # the rollback restores the frozen version
    monkeypatch.setenv("PORTFOLIOIQ_SQL_PROMPT", CANDIDATE_VARIANT)
    assert live_prompt_version(load_settings()) == "5219dec6dd3e"
