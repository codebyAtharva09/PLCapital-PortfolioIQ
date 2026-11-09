"""Candidate v2.4: separate from production v2.3. Held-out T01-T16 is deliberately absent from this file."""

import hashlib
import json
import re

import pytest

from portfolioiq.config import PROJECT_ROOT, load_settings
from portfolioiq.llm import LLMCall, LLMResult
from portfolioiq.semantic import CANDIDATE_22_EXAMPLES, CANDIDATE_24_EXAMPLES, SemanticContext
from portfolioiq.sql_engine import execute_sql, open_read_only
from portfolioiq.sql_policy_v24 import Vocabulary, assess, assess_result, read_intent
from portfolioiq.sql_tool import (CANDIDATE_22_VARIANT, CANDIDATE_23_VARIANT, CANDIDATE_24_INSTRUCTION, CANDIDATE_24_VARIANT, SqlDraft, SqlTool,
                                  sql_prompt_version, variant_for)
from portfolioiq_eval import official, sql_eval
from portfolioiq_eval.comparator import compare_results, requires_order

SHAPE = {c["id"]: c for c in json.loads((PROJECT_ROOT / "evaluation" / "sql_shape_set.json").read_text("utf-8"))["cases"]}


@pytest.fixture(scope="module")
def facts(readonly_db):
    conn = open_read_only(readonly_db)
    try:
        return Vocabulary(portfolios=tuple(r[0] for r in conn.execute("SELECT portfolio_name FROM portfolios")),
                          sectors=tuple(r[0] for r in conn.execute("SELECT sector_name FROM sectors")))
    finally:
        conn.close()


def errors(a):
    return {i.code for i in a.issues if i.severity == "error"}


@pytest.fixture()
def check(schema, facts, readonly_db):
    def run(question, sql):
        result = execute_sql(readonly_db, sql, max_rows=500)
        assessment = assess(question, sql, schema, facts)
        return assess_result(assessment, result.columns, result.rows) if result.status == "ok" else assessment
    return run


def saved(glob, field="result"):
    latest = {}
    for path in sorted((PROJECT_ROOT / "reports").glob(glob)):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                latest[record["case"]["id"]] = record
    found = {}
    for case_id, record in latest.items():
        sql = (record.get("result") or {}).get("sql") if field == "result" else ((record.get("receipt") or {}).get("sql") or {}).get("sql")
        if sql:
            found[case_id] = (record["case"]["question"], sql)
    return found


V23 = "sprint_v2_3/candidate/runs/*/sql_outputs.jsonl"
SHAPE_V23 = "shape_v2_3/runs/*/sql_outputs.jsonl"
BASELINE = "runs/*/sql_outputs.jsonl"
V22_AGENT = "sprint_v2_2/candidate/runs/*/official_outputs.jsonl"


# --- production v2.3 is untouched ----------------------------------------------------------------------------------

def test_production_v23_is_frozen_and_remains_the_default(readonly_db):
    source = (PROJECT_ROOT / "src" / "portfolioiq" / "sql_policy.py").read_text("utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(source.encode()).hexdigest() == "be55c0a0d3ec095056a0ed062d17c8b691fad3390b504288ba8e14604869957e"
    v23 = variant_for(CANDIDATE_23_VARIANT)
    assert hashlib.sha256(v23.instruction.encode()).hexdigest() == "aaa7d082faa95c863fc479ecb5c4777a2639589ca67cf5517e34de4db0e2677f"
    context = SemanticContext.from_db(readonly_db, examples=v23.examples, latest_rule=v23.latest_rule)
    assert (sql_prompt_version(context.text, v23.instruction), context.version) == ("314e91c11435", "d5f1098033f7")
    assert load_settings().sql_prompt_variant == CANDIDATE_23_VARIANT


def test_v24_is_separately_versioned_and_contains_no_evaluation_material(readonly_db):
    v24 = variant_for(CANDIDATE_24_VARIANT)
    context = SemanticContext.from_db(readonly_db, examples=v24.examples, latest_rule=v24.latest_rule)
    assert sql_prompt_version(context.text, v24.instruction) not in ("314e91c11435", "2ef54d48d231", "5219dec6dd3e", "d5a914eb71d5")
    assert v24.shape_policy == "2.4" and "latest date of the entity asked about" in context.text
    forbidden = re.compile(r"\bQ\d+\b|\b[UST]\d+\b|benchmark|ground.?truth|expected|reference|held.?out|Technology|Growth Equity", re.I)
    assert not forbidden.search(CANDIDATE_24_INSTRUCTION)
    assert [e.id for e, f in zip(CANDIDATE_22_EXAMPLES, CANDIDATE_24_EXAMPLES) if e != f] == ["VE4"]
    for added in ("returned on its own", "only the percentage", "that is the same for every row", "entity's own rows"):
        assert added in CANDIDATE_24_INSTRUCTION and added not in variant_for(CANDIDATE_23_VARIANT).instruction


# --- grouped aggregations ------------------------------------------------------------------------------------------

S10 = SHAPE["S10"]


def test_s10_reference_and_saved_draft_are_not_flagged(check):
    assert not errors(check(S10["question"], S10["reference_sql"]))
    assert not errors(check(*saved(SHAPE_V23)["S10"]))


def test_at_each_reads_as_grouping(schema, facts):
    intent = read_intent(S10["question"], schema, facts)
    assert (intent.grain, intent.group_entity) == ("per_group", "risk level")


def test_a_group_key_the_question_names_is_a_grouped_answer_even_when_the_wording_rules_miss_it(check):
    question = "What is the total stated AUM broken out over target risk level?"
    assert not errors(check(question, "SELECT target_risk_level, SUM(total_aum) AS aum FROM portfolios GROUP BY target_risk_level"))


def test_a_group_key_the_question_does_not_name_is_still_a_defect(check):
    found = errors(check("What is the total stated AUM of the portfolios?", "SELECT strategy_type, SUM(total_aum) AS aum FROM portfolios GROUP BY strategy_type"))
    assert {"unrelated_record_columns", "scalar_expected"} <= found


def test_rows_where_one_total_was_asked_for_are_still_a_defect(check):
    sql = "SELECT portfolio_name, total_aum FROM portfolios WHERE status = 'Passive'"
    assert "scalar_expected" in errors(check("What is the combined AUM of passive funds?", sql))


def test_a_non_group_column_beside_a_grouped_total_is_still_extra(check):
    sql = "SELECT portfolio_name, SUM(total_aum) AS aum FROM portfolios WHERE status = 'Passive'"
    assert "unrelated_record_columns" in errors(check("What is the combined AUM of passive funds?", sql))


# --- latest dates ------------------------------------------------------------------------------------------------

LATEST_Q = "What is the most recent beta for the Balanced Portfolio?"
JOIN = "FROM risk_metrics r JOIN portfolios p ON p.portfolio_id = r.portfolio_id WHERE p.portfolio_name = 'Balanced Portfolio'"


@pytest.mark.parametrize("sql", [
    f"SELECT r.beta {JOIN} AND r.calculation_date = (SELECT MAX(x.calculation_date) FROM risk_metrics x WHERE x.portfolio_id = r.portfolio_id)",
    f"SELECT r.beta {JOIN} ORDER BY r.calculation_date DESC LIMIT 1",
    f"SELECT r.beta {JOIN} AND r.calculation_date = (SELECT MAX(x.calculation_date) FROM risk_metrics x JOIN portfolios q ON q.portfolio_id = x.portfolio_id WHERE q.portfolio_name = 'Balanced Portfolio')",
    "WITH m AS (SELECT portfolio_id, MAX(calculation_date) AS d FROM risk_metrics GROUP BY portfolio_id) "
    f"SELECT r.beta FROM risk_metrics r JOIN m ON m.portfolio_id = r.portfolio_id AND m.d = r.calculation_date JOIN portfolios p ON p.portfolio_id = r.portfolio_id WHERE p.portfolio_name = 'Balanced Portfolio'",
    "SELECT beta FROM (SELECT r.beta, ROW_NUMBER() OVER (PARTITION BY r.portfolio_id ORDER BY r.calculation_date DESC) AS n "
    f"{JOIN}) AS t WHERE n = 1",
], ids=["correlated", "order-limit", "filtered-subquery", "grouped-cte", "window"])
def test_per_entity_latest_forms_are_accepted(schema, facts, sql):
    assert "global_latest_date" not in errors(assess(LATEST_Q, sql, schema, facts))


def test_table_wide_max_for_a_named_entity_is_caught(schema, facts):
    sql = f"SELECT r.beta {JOIN} AND r.calculation_date = (SELECT MAX(calculation_date) FROM risk_metrics)"
    assert errors(assess(LATEST_Q, sql, schema, facts)) == {"global_latest_date"}


def test_table_wide_max_in_a_cte_is_caught(schema, facts):
    sql = f"WITH m AS (SELECT MAX(calculation_date) AS d FROM risk_metrics) SELECT r.beta {JOIN} AND r.calculation_date = (SELECT d FROM m)"
    assert "global_latest_date" in errors(assess(LATEST_Q, sql, schema, facts))


def test_table_wide_max_for_each_entity_is_caught(schema, facts):
    question = "What is the latest Sharpe ratio of each portfolio?"
    sql = ("SELECT p.portfolio_name, x.sharpe_ratio FROM portfolio_performance x JOIN portfolios p ON p.portfolio_id = x.portfolio_id "
           "WHERE x.performance_date = (SELECT MAX(performance_date) FROM portfolio_performance)")
    assert "global_latest_date" in errors(assess(question, sql, schema, facts))


def test_an_explicitly_table_wide_latest_date_is_allowed(schema, facts):
    question = "What is the beta of the Balanced Portfolio on the latest date in the risk table?"
    sql = f"SELECT r.beta {JOIN} AND r.calculation_date = (SELECT MAX(calculation_date) FROM risk_metrics)"
    assert read_intent(question, schema, facts).latest_scope == "overall"
    assert "global_latest_date" not in errors(assess(question, sql, schema, facts))


def test_a_question_that_is_not_about_the_latest_value_is_not_affected(schema, facts):
    assert read_intent("What is the highest beta recorded for the Balanced Portfolio?", schema, facts).latest_scope is None


def test_the_recorded_table_wide_answers_are_caught_and_their_per_entity_form_is_identical_today(check, readonly_db):
    for case_id, drafts in (("U5", saved(V23)), ("S03", saved(SHAPE_V23))):
        assert errors(check(*drafts[case_id])) == {"global_latest_date"}
    corrected = ("SELECT r.tracking_error FROM risk_metrics r JOIN portfolios p ON p.portfolio_id = r.portfolio_id WHERE p.portfolio_name = 'Balanced Portfolio' "
                 "AND r.calculation_date = (SELECT MAX(x.calculation_date) FROM risk_metrics x WHERE x.portfolio_id = r.portfolio_id)")
    assert execute_sql(readonly_db, corrected).rows == execute_sql(readonly_db, saved(SHAPE_V23)["S03"][1]).rows


def test_the_new_few_shot_example_is_per_entity_and_valid(readonly_db):
    example = next(e for e in CANDIDATE_24_EXAMPLES if e.id == "VE4")
    assert "x.portfolio_id = pp.portfolio_id" in example.sql and execute_sql(readonly_db, example.sql).status == "ok"


# --- ratios and percentages ----------------------------------------------------------------------------------------

Q8 = next(c for c in official.load_cases() if c.id == "Q8")
RATIO_FROM = ("FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id JOIN securities s ON s.security_id = h.security_id "
              "GROUP BY p.portfolio_id, p.portfolio_name HAVING COUNT(DISTINCT s.sector_id) > 3")
RATIO = "CAST(COUNT(DISTINCT s.sector_id) AS REAL) / COUNT(h.holding_id) AS concentration"
OPERANDS = "COUNT(DISTINCT s.sector_id) AS n, COUNT(h.holding_id) AS m"


def test_q8_asks_for_metrics_so_its_operands_are_required(check):
    assert not errors(check(Q8.question, Q8.reference_sql))
    assert not errors(check(*saved(V23)["Q8"]))
    assert "missing_ratio_components" in errors(check(*saved(V22_AGENT, "receipt")["Q8"]))


def test_requested_operands_must_be_present(check):
    question = "Find funds with more than 3 sectors and show their concentration metrics"
    assert not errors(check(question, f"SELECT p.portfolio_name, {OPERANDS}, {RATIO} {RATIO_FROM}"))
    assert "missing_ratio_components" in errors(check(question, f"SELECT p.portfolio_name, {RATIO} {RATIO_FROM}"))


def test_operands_that_were_not_requested_are_extra(check):
    question = "Find funds with more than 3 sectors and show the concentration of each"
    assert not errors(check(question, f"SELECT p.portfolio_name, {RATIO} {RATIO_FROM}"))
    assert "unrequested_operands" in errors(check(question, f"SELECT p.portfolio_name, {OPERANDS}, {RATIO} {RATIO_FROM}"))


def test_s11_reference_passes_and_the_saved_draft_is_flagged_for_its_real_defect_only(check):
    assert not errors(check(SHAPE["S11"]["question"], SHAPE["S11"]["reference_sql"]))
    assert errors(check(*saved(SHAPE_V23)["S11"])) == {"unrequested_operands"}


def test_s17_reference_passes_and_the_saved_draft_is_flagged_for_its_real_defect_only(check):
    assert not errors(check(SHAPE["S17"]["question"], SHAPE["S17"]["reference_sql"]))
    assert errors(check(*saved(SHAPE_V23)["S17"])) == {"constant_whole_returned"}


def test_count_star_keeps_its_filters_when_traced(schema, facts):
    sql = ("WITH a AS (SELECT COUNT(*) AS n FROM securities WHERE asset_type = 'Bond'), b AS (SELECT COUNT(*) AS n FROM securities) "
           "SELECT ROUND(a.n * 100.0 / b.n, 2) AS pct FROM a, b")
    found = errors(assess("What percentage of all securities are bonds?", sql, schema, facts))
    assert "denominator_is_numerator" not in found and "percentage_hardcoded" not in found


def test_a_fixed_percentage_is_still_caught(check):
    assert "percentage_hardcoded" in errors(check("What percentage of all securities are bonds?", "SELECT 16.67 AS pct"))


def test_a_share_question_that_asks_for_the_count_keeps_it(check):
    sql = ("WITH a AS (SELECT COUNT(*) AS n FROM securities WHERE asset_type = 'Bond'), b AS (SELECT COUNT(*) AS t FROM securities) "
           "SELECT a.n, ROUND(a.n * 100.0 / b.t, 2) AS pct FROM a, b")
    assert "unrequested_operands" not in errors(check("How many securities are bonds, and what percentage of all securities is that?", sql))


# --- Q5, Q7 and every recorded answer -------------------------------------------------------------------------------

def test_q5_and_q7_regressions(check, readonly_db):
    for case_id in ("Q5", "Q7"):
        case = next(c for c in official.load_cases() if c.id == case_id)
        assert not errors(check(case.question, case.reference_sql))
        question, sql = saved(V23)[case_id]
        assert not errors(check(question, sql))
        reference, result = execute_sql(readonly_db, case.reference_sql, max_rows=1000), execute_sql(readonly_db, sql, max_rows=1000)
        assert compare_results(reference.columns, reference.rows, result.columns, result.rows, ordered=requires_order(case.reference_sql)).strict


def test_q5_missing_identity_and_q7_missing_own_whole_are_still_caught(check):
    drafts = saved(BASELINE)
    assert {"missing_record_identity", "missing_identity_column"} <= errors(check(*drafts["Q5"]))
    assert {"missing_denominator_column", "population_includes_non_qualifying"} <= errors(check(*drafts["Q7"]))


def test_extra_columns_on_lists_are_still_caught(check):
    assert errors(check(*saved(BASELINE)["U4"])) == {"over_projection"}


def test_references_are_flagged_only_where_they_use_the_table_wide_date(check):
    flagged = {}
    for case in [*official.load_cases(), *sql_eval.load_cases()]:
        if getattr(case, "reference_sql", None):
            found = errors(check(case.question, case.reference_sql))
            if found:
                flagged[case.id] = found
    for case_id, case in SHAPE.items():
        found = errors(check(case["question"], case["reference_sql"]))
        if found:
            flagged[case_id] = found
    assert flagged == {"U5": {"global_latest_date"}}  # that reference is not changed; every portfolio ends on the same date today


def test_recorded_v23_answers_are_flagged_only_for_the_known_issues(check):
    drafts = {**saved(V23), **saved(SHAPE_V23)}
    assert len(drafts) >= 35
    flagged = {cid: errors(check(q, sql)) for cid, (q, sql) in drafts.items()}
    assert {cid: codes for cid, codes in flagged.items() if codes} == {
        "U5": {"global_latest_date"}, "S03": {"global_latest_date"}, "S11": {"unrequested_operands"}, "S17": {"constant_whole_returned"}}


# --- the tool uses the right policy for each configuration -----------------------------------------------------------

class Scripted:
    model = "fake-model"

    def __init__(self, *items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        return LLMResult(value=self.items.pop(0), call=LLMCall(model="fake-model", sdk_version="t", latency_ms=1.0, attempts=1, request_hash="ef" * 32))


def draft(sql):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation="scripted")


def tool(readonly_db, variant, *items):
    configuration = variant_for(variant)
    context = SemanticContext.from_db(readonly_db, examples=configuration.examples, latest_rule=configuration.latest_rule)
    return SqlTool(readonly_db, context, Scripted(*items), variant=variant)


def test_v24_uses_its_own_policy_and_retries_the_table_wide_date(readonly_db):
    bad = f"SELECT r.beta {JOIN} AND r.calculation_date = (SELECT MAX(calculation_date) FROM risk_metrics)"
    good = f"SELECT r.beta {JOIN} ORDER BY r.calculation_date DESC LIMIT 1"
    result = tool(readonly_db, CANDIDATE_24_VARIANT, draft(bad), draft(good)).answer(LATEST_Q)
    assert result.sql == good and result.shape_policy.version == "2.4" and result.shape_policy.repaired


@pytest.mark.parametrize("variant", [CANDIDATE_22_VARIANT, CANDIDATE_23_VARIANT])
def test_earlier_configurations_keep_their_recorded_behaviour(readonly_db, variant):
    bad = f"SELECT r.beta {JOIN} AND r.calculation_date = (SELECT MAX(calculation_date) FROM risk_metrics)"
    result = tool(readonly_db, variant, draft(bad)).answer(LATEST_Q)
    assert result.sql == bad and len(result.attempts) == 1 and result.shape_policy.version == variant_for(variant).shape_policy
