"""Candidate SQL shape policy v2.1. Each negative test plants one real defect and asserts that defect is what is reported.

The questions here are written for these tests. The official references and the recorded Gemini drafts appear only in the
last section, as a positive control and a real-output check; no runtime code can see them.
"""

import json

import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.semantic import CANDIDATE_EXAMPLES, SemanticContext
from portfolioiq.sql_engine import execute_sql, open_read_only
from portfolioiq.sql_policy import Vocabulary, assess, assess_result, read_intent
from portfolioiq.sql_tool import BASELINE_VARIANT, CANDIDATE_VARIANT, SqlDraft, SqlTool
from portfolioiq.verification import CheckStatus, VerificationStatus
from portfolioiq_eval import official, sql_eval
from portfolioiq_eval.comparator import compare_results, requires_order


@pytest.fixture(scope="module")
def facts(readonly_db):
    conn = open_read_only(readonly_db)
    try:
        vocabulary = Vocabulary(portfolios=tuple(r[0] for r in conn.execute("SELECT portfolio_name FROM portfolios")),
                                sectors=tuple(r[0] for r in conn.execute("SELECT sector_name FROM sectors")))
    finally:
        conn.close()
    return vocabulary


@pytest.fixture()
def check(schema, facts):
    return lambda question, sql: assess(question, sql, schema, facts)


def errors(assessment):
    return {i.code for i in assessment.issues if i.severity == "error"}


def warnings(assessment):
    return {i.code for i in assessment.issues if i.severity == "warning"}


JOINS = ("FROM holdings h JOIN securities s ON s.security_id = h.security_id JOIN portfolios p ON p.portfolio_id = h.portfolio_id "
         "WHERE p.portfolio_name = 'Balanced Portfolio'")
RANK_Q = "Show the top 3 holdings by cost basis in the Balanced Portfolio"
FULL = "h.holding_id, s.symbol, s.company_name, h.quantity, h.cost_basis"
RANK_OK = f"SELECT {FULL} {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"

SHARE_Q = "For each portfolio, show the total value of Healthcare sector holdings and what percentage it represents of the total portfolio value"
PART = ("part AS (SELECT p.portfolio_id, p.portfolio_name, SUM(h.quantity * s.current_price) AS part_value FROM portfolios p "
        "JOIN holdings h ON h.portfolio_id = p.portfolio_id JOIN securities s ON s.security_id = h.security_id "
        "JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Healthcare' GROUP BY p.portfolio_id, p.portfolio_name)")
WHOLE = ("whole AS (SELECT h.portfolio_id, SUM(h.quantity * s.current_price) AS total_value FROM holdings h "
         "JOIN securities s ON s.security_id = h.security_id GROUP BY h.portfolio_id)")
OUTER = "FROM part JOIN whole ON whole.portfolio_id = part.portfolio_id ORDER BY pct DESC"
SHARE_OK = (f"WITH {PART}, {WHOLE} SELECT part.portfolio_name, part.part_value, whole.total_value, "
            f"ROUND(part.part_value * 100.0 / whole.total_value, 2) AS pct {OUTER}")


def share_with(percentage: str, *, whole: str = WHOLE, columns: str = "part.portfolio_name, part.part_value, whole.total_value") -> str:
    return f"WITH {PART}, {whole} SELECT {columns}, {percentage} AS pct {OUTER}"


# --- the shapes that must be accepted ------------------------------------------------------------------------------

def test_a_complete_ranked_holdings_list_is_clean(check):
    assert not check(RANK_Q, RANK_OK).issues


def test_a_complete_share_query_has_no_errors_and_records_the_population_assumption(check):
    result = check(SHARE_Q, SHARE_OK)
    assert errors(result) == set()
    assert "population_assumed" in warnings(result)


def test_aliases_are_judged_by_their_source_not_their_name(check):
    sql = (f"SELECT h.holding_id AS id, s.symbol AS ticker, s.company_name AS company, h.quantity AS units, h.cost_basis AS basis "
           f"{JOINS} ORDER BY basis DESC LIMIT 3")
    assert not errors(check(RANK_Q, sql))


def test_a_ranking_written_through_a_cte_is_traced_to_its_columns(check):
    sql = (f"WITH r AS (SELECT {FULL} {JOINS}) SELECT holding_id, symbol, company_name, quantity, cost_basis FROM r "
           f"ORDER BY cost_basis DESC LIMIT 3")
    assert not errors(check(RANK_Q, sql))


def test_a_one_query_share_written_without_ctes_has_its_roles_recognised(check):
    question = "What percentage of the Balanced Portfolio's portfolio value is in Healthcare stocks?"
    sql = ("SELECT SUM(CASE WHEN sec.sector_name = 'Healthcare' THEN h.quantity * s.current_price ELSE 0 END) * 100.0 "
           "/ SUM(h.quantity * s.current_price) AS pct FROM holdings h JOIN securities s ON s.security_id = h.security_id "
           "JOIN portfolios p ON p.portfolio_id = h.portfolio_id LEFT JOIN sectors sec ON sec.sector_id = s.sector_id "
           "WHERE p.portfolio_name = 'Balanced Portfolio'")
    assert not errors(check(question, sql))


# --- 1 to 6: projection and ranking --------------------------------------------------------------------------------

def test_01_missing_required_output_column(check):
    assert "missing_identity_column" in errors(check(RANK_Q, f"SELECT h.holding_id, s.symbol, s.company_name, h.cost_basis {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"))


def test_02_correct_column_name_with_the_wrong_source_expression(check):
    sql = f"SELECT h.holding_id, s.symbol, s.company_name, h.purchase_price AS quantity, h.cost_basis {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"
    assert "wrong_source_expression" in errors(check(RANK_Q, sql))


def test_03_incorrect_ordering_metric(check):
    assert errors(check(RANK_Q, f"SELECT {FULL} {JOINS} ORDER BY h.quantity DESC LIMIT 3")) == {"wrong_order_metric"}


def test_04_incorrect_ordering_direction(check):
    assert errors(check(RANK_Q, f"SELECT {FULL} {JOINS} ORDER BY h.cost_basis ASC LIMIT 3")) == {"wrong_order_direction"}
    assert errors(check(RANK_Q, f"SELECT {FULL} {JOINS} ORDER BY h.cost_basis LIMIT 3")) == {"wrong_order_direction"}  # SQLite's default


def test_05_missing_limit(check):
    assert errors(check(RANK_Q, f"SELECT {FULL} {JOINS} ORDER BY h.cost_basis DESC")) == {"missing_limit"}


@pytest.mark.parametrize("tail", ["LIMIT 5", "LIMIT 1", "LIMIT 3 OFFSET 1"])
def test_06_incorrect_limit(check, tail):
    assert errors(check(RANK_Q, f"SELECT {FULL} {JOINS} ORDER BY h.cost_basis DESC {tail}")) == {"wrong_limit"}


def test_metric_used_only_in_where_is_not_a_ranking(check):
    sql = f"SELECT {FULL} {JOINS} AND h.cost_basis > 0 ORDER BY h.quantity DESC LIMIT 3"
    assert errors(check(RANK_Q, sql)) == {"wrong_order_metric"}


def test_order_by_inside_a_subquery_does_not_rank_the_final_result(check):
    sql = f"SELECT holding_id, symbol, company_name, quantity, cost_basis FROM (SELECT {FULL} {JOINS} ORDER BY h.cost_basis DESC LIMIT 3) AS t"
    found = errors(check(RANK_Q, sql))
    assert "order_only_in_subquery" in found and "missing_limit" in found


def test_correct_values_without_the_identity_columns_are_caught(check):
    assert errors(check(RANK_Q, f"SELECT s.symbol, s.company_name, h.cost_basis {JOINS} ORDER BY h.cost_basis DESC LIMIT 3")) >= {
        "missing_record_identity", "missing_identity_column"}


def test_a_column_inside_an_expression_is_not_a_projected_column(check):
    sql = f"SELECT h.holding_id, s.symbol, s.company_name, h.quantity * 2 AS doubled, h.cost_basis {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"
    assert "missing_identity_column" in errors(check(RANK_Q, sql))


def test_columns_used_only_in_where_or_join_are_not_projected(check):
    sql = f"SELECT s.symbol, s.company_name, h.cost_basis {JOINS} AND h.holding_id > 0 AND h.quantity > 0 ORDER BY h.cost_basis DESC LIMIT 3"
    assert "missing_record_identity" in errors(check(RANK_Q, sql))


def test_14_nested_cte_projection_is_not_mistaken_for_the_final_result(check):
    sql = f"WITH r AS (SELECT {FULL} {JOINS}) SELECT symbol, cost_basis FROM r ORDER BY cost_basis DESC LIMIT 3"
    assert errors(check(RANK_Q, sql)) >= {"missing_record_identity", "missing_identity_column"}


def test_grouped_rows_are_not_individual_holdings(check):
    sql = f"SELECT h.holding_id, s.symbol, s.company_name, h.quantity, SUM(h.cost_basis) AS cost_basis {JOINS} GROUP BY s.symbol ORDER BY cost_basis DESC LIMIT 3"
    assert "grain_mismatch" in errors(check(RANK_Q, sql))


# --- 7 to 10, 15: the percentage ---------------------------------------------------------------------------------

def test_07_percentage_alias_over_a_formula_with_no_division(check):
    assert "percentage_without_division" in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100, 2)")))


def test_dividing_by_a_fixed_number_is_not_a_percentage_of_a_total(check):
    assert "percentage_without_division" in errors(check(SHARE_Q, share_with("part.part_value / 100.0")))


def test_08_numerator_used_as_denominator(check):
    assert "denominator_is_numerator" in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / part.part_value, 2)")))


def test_09_hardcoded_percentage(check):
    assert "percentage_hardcoded" in errors(check(SHARE_Q, share_with("37.5")))


def test_10_bond_value_omitted_by_an_asset_filter(check):
    whole = ("whole AS (SELECT h.portfolio_id, SUM(h.quantity * s.current_price) AS total_value FROM holdings h JOIN securities s "
             "ON s.security_id = h.security_id WHERE s.asset_type = 'Stock' GROUP BY h.portfolio_id)")
    assert "denominator_filtered" in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / whole.total_value, 2)", whole=whole)))


def test_10_bond_value_omitted_by_an_inner_join_to_sectors(check):
    whole = ("whole AS (SELECT h.portfolio_id, SUM(h.quantity * s.current_price) AS total_value FROM holdings h JOIN securities s "
             "ON s.security_id = h.security_id JOIN sectors sec ON sec.sector_id = s.sector_id GROUP BY h.portfolio_id)")
    assert "denominator_drops_bonds" in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / whole.total_value, 2)", whole=whole)))


def test_a_left_join_to_sectors_keeps_bonds_in_the_total(check):
    whole = ("whole AS (SELECT h.portfolio_id, SUM(h.quantity * s.current_price) AS total_value FROM holdings h JOIN securities s "
             "ON s.security_id = h.security_id LEFT JOIN sectors sec ON sec.sector_id = s.sector_id GROUP BY h.portfolio_id)")
    assert not errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / whole.total_value, 2)", whole=whole)))


def test_a_misleading_alias_is_exposed_by_tracing_the_expression(check):
    sql = share_with("ROUND(part.part_value * 100.0 / whole.total_value, 2)",
                     columns="part.portfolio_name, part.part_value AS total_value, whole.total_value AS grand")
    assert "misleading_alias" in errors(check(SHARE_Q, sql))


def test_the_percentage_division_is_resolved_through_a_misleadingly_named_cte_column(check):
    whole = ("whole AS (SELECT h.portfolio_id, SUM(CASE WHEN s.sector_id IS NOT NULL THEN h.quantity * s.current_price END) AS total_value "
             "FROM holdings h JOIN securities s ON s.security_id = h.security_id WHERE s.asset_type = 'Stock' GROUP BY h.portfolio_id)")
    assert "denominator_filtered" in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / whole.total_value, 2)", whole=whole)))


def test_a_ratio_turned_upside_down(check):
    assert "ratio_inverted" in errors(check(SHARE_Q, share_with("ROUND(whole.total_value * 100.0 / part.part_value, 2)")))


def test_a_part_that_is_not_restricted_to_the_requested_sector(check):
    part = ("part AS (SELECT p.portfolio_id, p.portfolio_name, SUM(h.quantity * s.current_price) AS part_value FROM portfolios p "
            "JOIN holdings h ON h.portfolio_id = p.portfolio_id JOIN securities s ON s.security_id = h.security_id "
            "JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Energy' GROUP BY p.portfolio_id, p.portfolio_name)")
    sql = (f"WITH {part}, {WHOLE} SELECT part.portfolio_name, part.part_value, whole.total_value, "
           f"ROUND(part.part_value * 100.0 / whole.total_value, 2) AS pct {OUTER}")
    assert "numerator_missing_subset" in errors(check(SHARE_Q, sql))


def test_a_fraction_is_not_a_percentage(check):
    assert "percentage_missing_scale" in errors(check(SHARE_Q, share_with("ROUND(part.part_value / whole.total_value, 4)")))


@pytest.mark.parametrize("column", ["total_aum", "current_weight"])
def test_15_valid_sql_with_the_wrong_financial_source_for_the_total(check, column):
    source = ("portfolios p" if column == "total_aum" else "portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id")
    prefix = "p" if column == "total_aum" else "h"
    whole = f"whole AS (SELECT p.portfolio_id, SUM({prefix}.{column}) AS total_value FROM {source} GROUP BY p.portfolio_id)"
    assert "wrong_denominator_source" in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / whole.total_value, 2)", whole=whole)))


def test_15_valid_sql_that_ranks_by_the_wrong_kind_of_value(check):
    question = "Show the top 3 holdings by market value in the Balanced Portfolio"
    sql = f"SELECT {FULL} {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"
    assert "wrong_order_metric" in errors(check(question, sql))


def test_division_by_literal_zero(check):
    assert "denominator_literal_zero" in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / 0, 2)")))


def test_an_unguarded_denominator_is_a_warning_and_is_settled_by_the_rows(check):
    assert "denominator_zero_unguarded" in warnings(check(SHARE_Q, SHARE_OK))
    guarded = share_with("ROUND(part.part_value * 100.0 / NULLIF(whole.total_value, 0), 2)")
    assert "denominator_zero_unguarded" not in warnings(check(SHARE_Q, guarded))


def test_integer_division_truncates_and_is_caught(check):
    question = "What percentage of the holdings in the Balanced Portfolio are bonds?"
    truncated = (f"SELECT SUM(CASE WHEN s.asset_type = 'Bond' THEN 1 ELSE 0 END) * 100 / COUNT(*) AS pct {JOINS}")
    real = (f"SELECT SUM(CASE WHEN s.asset_type = 'Bond' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct {JOINS}")
    assert "integer_division" in errors(check(question, truncated))
    assert "integer_division" not in errors(check(question, real))


@pytest.mark.parametrize("columns, code", [
    ("part.portfolio_name, whole.total_value", "missing_numerator_column"),
    ("part.portfolio_name, part.part_value", "missing_denominator_column"),
])
def test_the_parts_of_the_percentage_must_be_returned(check, columns, code):
    assert code in errors(check(SHARE_Q, share_with("ROUND(part.part_value * 100.0 / whole.total_value, 2)", columns=columns)))


def test_no_percentage_at_all(check):
    assert "missing_percentage_column" in errors(check(SHARE_Q, f"WITH {PART} SELECT part.portfolio_name, part.part_value FROM part"))


# --- 12: population ---------------------------------------------------------------------------------------------

ZERO_FILLED = ("SELECT p.portfolio_name, SUM(CASE WHEN sec.sector_name = 'Healthcare' THEN h.quantity * s.current_price ELSE 0 END) AS part_value, "
               "SUM(h.quantity * s.current_price) AS total_value, "
               "ROUND(SUM(CASE WHEN sec.sector_name = 'Healthcare' THEN h.quantity * s.current_price ELSE 0 END) * 100.0 / SUM(h.quantity * s.current_price), 2) AS pct "
               "FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id JOIN securities s ON s.security_id = h.security_id "
               "LEFT JOIN sectors sec ON sec.sector_id = s.sector_id GROUP BY p.portfolio_id, p.portfolio_name ORDER BY pct DESC")


def test_12_the_ambiguous_for_each_follows_the_one_population_rule(check):
    result = check(SHARE_Q, ZERO_FILLED)
    assert "population_includes_non_qualifying" in errors(result)
    assert "population_assumed" in warnings(check(SHARE_Q, SHARE_OK))


def test_a_left_join_with_coalesce_also_adds_entities_with_nothing(check):
    sql = (f"WITH {PART}, {WHOLE} SELECT p.portfolio_name, COALESCE(part.part_value, 0) AS part_value, whole.total_value, "
           f"ROUND(COALESCE(part.part_value, 0) * 100.0 / whole.total_value, 2) AS pct FROM portfolios p "
           f"JOIN whole ON whole.portfolio_id = p.portfolio_id LEFT JOIN part ON part.portfolio_id = p.portfolio_id ORDER BY pct DESC")
    assert "population_includes_non_qualifying" in errors(check(SHARE_Q, sql))


def test_when_the_question_says_all_portfolios_none_may_be_left_out(check):
    question = "For all portfolios, including those with none, show the value of Healthcare holdings and its percentage of the total portfolio value"
    assert "population_excludes_requested" in errors(check(question, SHARE_OK))
    assert "population_includes_non_qualifying" not in errors(check(question, ZERO_FILLED))


def test_a_condition_stated_in_the_question_needs_no_assumption(check):
    question = "For each portfolio that holds Healthcare stocks, show that value and its percentage of the total portfolio value"
    assert "population_assumed" not in warnings(check(question, SHARE_OK))


# --- 11, 13: over-projection and duplicates ---------------------------------------------------------------------

BONDS_Q = "Give each bond's coupon rate and listing exchange"


def test_11_a_bond_attribute_list_does_not_get_company_fields(check):
    sql = "SELECT symbol, company_name, coupon_rate, exchange FROM securities WHERE asset_type = 'Bond'"
    assert errors(check(BONDS_Q, sql)) == {"over_projection"}
    assert not errors(check(BONDS_Q, "SELECT symbol, coupon_rate, exchange FROM securities WHERE asset_type = 'Bond'"))


def test_11_a_filter_field_is_not_returned(check):
    sql = ("SELECT s.symbol, s.company_name, sec.sector_name FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id "
           "WHERE sec.sector_name = 'Energy'")
    assert errors(check("Which securities are in the Energy sector?", sql)) == {"over_projection"}


def test_11_extra_columns_on_a_holdings_list(check):
    sql = f"SELECT {FULL}, h.current_weight, p.portfolio_name {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"
    assert errors(check(RANK_Q, sql)) == {"over_projection"}


def test_a_calculated_extra_on_a_holdings_list_is_over_projection(check):
    sql = f"SELECT {FULL}, h.quantity * s.current_price AS market_value {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"
    assert "over_projection" in errors(check(RANK_Q, sql))


def test_a_join_key_is_not_an_answer(check):
    sql = ("SELECT s.security_id, s.symbol, s.company_name FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id "
           "WHERE sec.sector_name = 'Energy'")
    assert "join_key_in_output" in errors(check("Which securities are in the Energy sector?", sql))


def test_portfolio_identity_may_accompany_a_list_that_spans_portfolios(check):
    sql = ("SELECT h.holding_id, s.symbol, s.company_name, h.quantity, h.cost_basis, p.portfolio_name FROM holdings h "
           "JOIN securities s ON s.security_id = h.security_id JOIN portfolios p ON p.portfolio_id = h.portfolio_id ORDER BY h.cost_basis DESC LIMIT 3")
    assert not errors(check("Show the top 3 holdings by cost basis across the funds", sql))


def test_an_identity_question_needs_the_whole_identity(check):
    sql = "SELECT s.symbol FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Energy'"
    assert "missing_identity_column" in errors(check("Which securities are in the Energy sector?", sql))


def test_a_single_total_does_not_return_record_columns(check):
    sql = "SELECT portfolio_name, SUM(total_aum) AS aum FROM portfolios WHERE status = 'Passive'"
    assert "unrelated_record_columns" in errors(check("What is the combined AUM of passive funds?", sql))


def test_13_the_same_column_twice(check):
    sql = f"SELECT h.holding_id, s.symbol, s.company_name, h.quantity, h.cost_basis, h.cost_basis AS basis2 {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"
    assert "duplicate_output_column" in errors(check(RANK_Q, sql))


def test_13_a_join_that_repeats_entities_is_found_in_the_rows(check, readonly_db):
    question = "Which portfolios hold bonds?"
    sql = ("SELECT p.portfolio_name FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id "
           "JOIN securities s ON s.security_id = h.security_id WHERE s.asset_type = 'Bond'")
    result = execute_sql(readonly_db, sql)
    assert "duplicate_rows" in errors(assess_result(check(question, sql), result.columns, result.rows))


# --- what only executed rows can settle ---------------------------------------------------------------------------

def share_result(check, rows, *, portfolio_values=None):
    columns = ["portfolio_name", "part_value", "total_value", "pct"]
    return assess_result(check(SHARE_Q, SHARE_OK), columns, rows, portfolio_values=portfolio_values)


def test_correct_rows_pass_the_result_checks(check):
    assert not errors(share_result(check, [["A", 25.0, 100.0, 25.0], ["B", 1.0, 3.0, 33.33]]))


@pytest.mark.parametrize("row, code", [
    (["A", 25.0, 100.0, 40.0], "percentage_mismatch"),
    (["A", 250.0, 100.0, 250.0], "numerator_exceeds_denominator"),
    (["A", 25.0, 0, None], "denominator_zero"),
    (["A", 25.0, None, None], "denominator_zero"),
    (["A", 0, 100.0, 0.0], "population_includes_non_qualifying"),
])
def test_forced_mismatches_in_the_rows_are_caught(check, row, code):
    assert code in errors(share_result(check, [row]))


def test_the_total_is_compared_with_an_independently_computed_value(check):
    assert "denominator_mismatch" in errors(share_result(check, [["A", 25.0, 100.0, 25.0]], portfolio_values={"A": 150.0}))
    assert not errors(share_result(check, [["A", 25.0, 100.0, 25.0]], portfolio_values={"A": 100.0}))


def test_a_ranking_is_also_checked_in_the_rows(check):
    result = check(RANK_Q, RANK_OK)
    assert "ranking_not_sorted" in errors(assess_result(result, [o.name for o in result.shape.outputs],
                                                         [[1, "A", "a", 1, 5.0], [2, "B", "b", 1, 9.0]]))


def test_a_list_where_one_figure_was_asked_for(check, readonly_db):
    question = "What is the combined AUM of passive funds?"
    sql = "SELECT portfolio_name, total_aum FROM portfolios WHERE status = 'Passive'"
    result = execute_sql(readonly_db, sql)
    assert "scalar_expected" in errors(assess_result(check(question, sql), result.columns, result.rows))


# --- reading the question: the entity asked about is not any table that happens to be joined ----------------------

@pytest.mark.parametrize("question, entity, operation, grain", [
    ("Which funds have holdings in more than 3 sectors?", "portfolio", "list", "per_record"),
    ("Which portfolios hold Pfizer?", "portfolio", "list", "per_record"),
    ("How many holdings does the Balanced Portfolio have?", "holding", "aggregate", "scalar"),
    ("Show the top 3 holdings by quantity in the Balanced Portfolio", "holding", "rank", "per_record"),
    ("List each bond with its exchange", "security", "list", "per_record"),
    ("Which sector has the most stocks?", "sector", "rank", "per_record"),
    ("What is the average market cap of stocks in each sector?", "security", "aggregate", "per_group"),
    ("Show every transaction made by the Balanced Portfolio", "transaction", "list", "per_record"),
])
def test_primary_entity_and_operation(schema, facts, question, entity, operation, grain):
    intent = read_intent(question, schema, facts)
    assert (intent.primary_entity, intent.operation, intent.grain) == (entity, operation, grain)


def test_a_portfolio_or_sector_name_is_not_an_entity_word(schema, facts):
    assert read_intent("What is the risk level of the Balanced Portfolio?", schema, facts).primary_entity is None
    assert read_intent("Which stocks are in the Energy sector?", schema, facts).primary_entity == "security"


@pytest.mark.parametrize("question, top, direction", [
    ("top 3 holdings by quantity", 3, "desc"),
    ("the five cheapest stocks", 5, "asc"),
    ("Which three funds have the largest AUM?", 3, "desc"),
    ("Which one has the largest AUM?", None, "desc"),
    ("portfolios with at least ten holdings", None, None),
    ("Most recent beta for the Balanced Portfolio?", None, None),
])
def test_ranking_words(schema, facts, question, top, direction):
    intent = read_intent(question, schema, facts)
    assert (intent.top_n, intent.direction) == (top, direction)


def test_a_ratio_metric_is_not_a_share_question(schema, facts):
    assert read_intent("What is the Sharpe ratio of the Balanced Portfolio?", schema, facts).operation == "list"


# --- v2.2: the entity is already known, or the measure only chooses ------------------------------------------------

LOOKUP_Q = "Most recent Sharpe ratio for the Balanced Portfolio?"
LOOKUP_FROM = ("FROM portfolio_performance pp JOIN portfolios p ON p.portfolio_id = pp.portfolio_id WHERE p.portfolio_name = 'Balanced Portfolio' "
               "AND pp.performance_date = (SELECT MAX(performance_date) FROM portfolio_performance)")
WHICH_Q = "Which sector has the most bonds and stocks?"
WHICH_FROM = "FROM sectors sec JOIN securities s ON s.sector_id = sec.sector_id GROUP BY sec.sector_id, sec.sector_name ORDER BY COUNT(*) DESC LIMIT 1"


def test_a_lookup_on_a_named_entity_returns_only_the_attribute(check):
    assert not errors(check(LOOKUP_Q, f"SELECT pp.sharpe_ratio {LOOKUP_FROM}"))


@pytest.mark.parametrize("extra", ["p.portfolio_name", "pp.performance_date"])
def test_over_projection_on_a_lookup_filter_or_date_field(check, extra):
    assert errors(check(LOOKUP_Q, f"SELECT {extra}, pp.sharpe_ratio {LOOKUP_FROM}")) == {"over_projection"}


def test_a_date_the_question_asks_for_is_kept(check):
    assert not errors(check("Most recent Sharpe ratio for the Balanced Portfolio, and the date?", f"SELECT pp.performance_date, pp.sharpe_ratio {LOOKUP_FROM}"))


def test_a_lookup_nested_in_a_cte_is_judged_at_the_outermost_select(check):
    inner = f"WITH latest AS (SELECT p.portfolio_name, pp.performance_date, pp.sharpe_ratio {LOOKUP_FROM})"
    assert not errors(check(LOOKUP_Q, f"{inner} SELECT sharpe_ratio FROM latest"))
    assert errors(check(LOOKUP_Q, f"{inner} SELECT portfolio_name, sharpe_ratio FROM latest")) == {"over_projection"}


def test_which_has_the_most_returns_the_entity_and_only_sorts_by_the_measure(check):
    assert not errors(check(WHICH_Q, f"SELECT sec.sector_name {WHICH_FROM}"))
    assert "over_projection" in errors(check(WHICH_Q, f"SELECT sec.sector_name, COUNT(*) AS n {WHICH_FROM}"))


def test_a_measure_in_a_nested_query_is_not_a_projection(check):
    sql = ("WITH c AS (SELECT sec.sector_name, COUNT(*) AS n FROM sectors sec JOIN securities s ON s.sector_id = sec.sector_id "
           "GROUP BY sec.sector_name) SELECT sector_name FROM c ORDER BY n DESC LIMIT 1")
    assert not errors(check(WHICH_Q, sql))
    assert "over_projection" in errors(check(WHICH_Q, sql.replace("SELECT sector_name FROM c", "SELECT sector_name, n FROM c")))


@pytest.mark.parametrize("question", ["Which sector has the most stocks? Show the count along with it.", "Which sector has the most stocks, and their number?"])
def test_a_measure_the_question_asks_to_see_is_kept(check, question):
    if "along with" in question:
        assert not errors(check(question, f"SELECT sec.sector_name, COUNT(*) AS n {WHICH_FROM}"))


def test_which_fund_has_the_largest_returns_the_name_without_the_metric(check):
    question = "Which fund has the largest AUM?"
    good = "SELECT portfolio_name FROM portfolios ORDER BY total_aum DESC LIMIT 1"
    assert not errors(check(question, good))
    assert errors(check(question, good.replace("portfolio_name", "portfolio_name, total_aum", 1))) == {"over_projection"}


def test_incorrect_identity_fields_are_not_a_substitute(check):
    assert "join_key_in_output" in errors(check("Which fund has the largest AUM?", "SELECT portfolio_id FROM portfolios ORDER BY total_aum DESC LIMIT 1"))
    sql = "SELECT security_id, company_name FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Energy'"
    found = errors(check("Which securities are in the Energy sector?", sql))
    assert "join_key_in_output" in found and "missing_identity_column" in found


def test_top_n_lists_of_records_still_show_their_metric(check):
    assert not errors(check(RANK_Q, RANK_OK))
    assert "missing_ranking_metric" in errors(check(RANK_Q, f"SELECT h.holding_id, s.symbol, s.company_name, h.quantity {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"))


RATIO_Q = "Find funds with more than 3 sectors and show how concentrated they are"
RATIO_FROM = ("FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id JOIN securities s ON s.security_id = h.security_id "
              "GROUP BY p.portfolio_id, p.portfolio_name HAVING COUNT(DISTINCT s.sector_id) > 3")


def test_a_calculated_ratio_without_its_components(check):
    sql = f"SELECT p.portfolio_name, CAST(COUNT(DISTINCT s.sector_id) AS REAL) / COUNT(h.holding_id) AS concentration {RATIO_FROM}"
    assert "missing_ratio_components" in errors(check(RATIO_Q, sql))


def test_a_calculated_ratio_with_only_one_component(check):
    sql = f"SELECT p.portfolio_name, COUNT(DISTINCT s.sector_id) AS n, CAST(COUNT(DISTINCT s.sector_id) AS REAL) / COUNT(h.holding_id) AS concentration {RATIO_FROM}"
    assert "missing_ratio_components" in errors(check(RATIO_Q, sql))


def test_a_calculated_ratio_with_both_components_including_through_ctes(check):
    direct = (f"SELECT p.portfolio_name, COUNT(DISTINCT s.sector_id) AS n, COUNT(h.holding_id) AS m, "
              f"CAST(COUNT(DISTINCT s.sector_id) AS REAL) / COUNT(h.holding_id) AS concentration {RATIO_FROM}")
    assert not errors(check(RATIO_Q, direct))
    nested = (f"WITH c AS (SELECT p.portfolio_name, COUNT(DISTINCT s.sector_id) AS n, COUNT(h.holding_id) AS m {RATIO_FROM}) "
              f"SELECT portfolio_name, n, m, ROUND(CAST(n AS REAL) / m, 3) AS concentration FROM c")
    assert not errors(check(RATIO_Q, nested))


def test_a_stored_ratio_column_needs_no_components(check):
    assert not errors(check("What is the Sharpe ratio of the Balanced Portfolio?", f"SELECT pp.sharpe_ratio {LOOKUP_FROM}"))


def test_v22_live_draft_of_the_ratio_question_is_caught_by_v23(schema, facts, readonly_db):
    drafts = _saved_drafts("sprint_v2_2/candidate/runs/*/official_outputs.jsonl", "receipt")
    assert "missing_ratio_components" in errors(assessed(*drafts["Q8"], schema, facts, readonly_db))
    assert not errors(assessed(*drafts["Q5"], schema, facts, readonly_db)) and not errors(assessed(*drafts["Q7"], schema, facts, readonly_db))


def test_version_21_keeps_its_recorded_behaviour(schema, facts):
    sql = f"SELECT p.portfolio_name, pp.sharpe_ratio {LOOKUP_FROM}"
    assert errors(assess(LOOKUP_Q, sql, schema, facts, version="2.2")) == {"over_projection"}
    assert not errors(assess(LOOKUP_Q, sql, schema, facts, version="2.1"))
    assert assess(LOOKUP_Q, sql, schema, facts, version="2.1").report().version == "2.1"


# --- honest behaviour -------------------------------------------------------------------------------------------

def test_the_policy_reports_rather_than_repairs():
    from portfolioiq.sql_policy import PolicyAssessment

    assert hasattr(PolicyAssessment, "needs_repair") and hasattr(PolicyAssessment, "suggested_repairs")
    assert not hasattr(PolicyAssessment, "repairable")


def test_it_gives_up_visibly_on_what_it_cannot_read(check):
    assert warnings(check(RANK_Q, "SELECT 1 UNION SELECT 2")) == {"not_analysed"}
    assert not errors(check(RANK_Q, "SELECT 1 UNION SELECT 2"))
    assert warnings(check(RANK_Q, "this is not sql (")) == {"not_analysed"}
    assert warnings(check("Show the holdings of the Balanced Portfolio", f"SELECT * {JOINS}")) == {"star_projection"}


# --- the tool: bounded retry, never at the cost of a safe result or of safety ------------------------------------

class Scripted:
    model = "fake-model"

    def __init__(self, *items):
        self.items, self.prompts = list(items), []

    def generate(self, *, system, prompt, schema, purpose=""):
        self.prompts.append(prompt)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(value=item, call=LLMCall(model="fake-model", sdk_version="t", latency_ms=1.0, attempts=1, request_hash="ef" * 32))


def draft(sql):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation="scripted query")


@pytest.fixture()
def candidate(readonly_db):
    context = SemanticContext.from_db(readonly_db, examples=CANDIDATE_EXAMPLES)
    return lambda llm: SqlTool(readonly_db, context, llm, variant=CANDIDATE_VARIANT)


BAD = f"SELECT s.symbol, s.company_name, h.cost_basis {JOINS} ORDER BY h.cost_basis DESC LIMIT 3"


def test_a_flagged_draft_is_retried_once_and_the_retry_is_used(candidate):
    llm = Scripted(draft(BAD), draft(RANK_OK))
    result = candidate(llm).answer(RANK_Q)
    assert result.status == "ok" and result.sql == RANK_OK and len(result.attempts) == 2 and result.llm_calls == 2
    assert result.shape_policy.repaired and not [f for f in result.shape_policy.findings if f.severity == "error"]
    assert "holding_id" in llm.prompts[1] and "Your previous SQL" in llm.prompts[1]
    assert result.verification is VerificationStatus.RESULT_VALIDATED


def test_a_clean_draft_costs_no_retry(candidate):
    llm = Scripted(draft(RANK_OK))
    assert candidate(llm).answer(RANK_Q).llm_calls == 1


@pytest.mark.parametrize("second, reason", [
    (LLMError("unavailable", "503", attempts=1), "retry request failed"),
    (draft("DELETE FROM holdings"), "retry was rejected"),
    (SqlDraft(answerable=False, decline_reason="not_in_data", sql="", explanation="no"), "declined"),
    (draft(f"SELECT no_such_column {JOINS}"), "retry was rejected"),
])
def test_a_failed_retry_keeps_the_safe_first_result_and_says_so(candidate, second, reason):
    result = candidate(Scripted(draft(BAD), second)).answer(RANK_Q)
    assert result.status == "ok" and result.sql == BAD and result.execution.row_count == 3
    assert reason in result.shape_policy.retry_failed
    check = next(c for c in result.checks if c.name == "Result fits the question")
    assert check.status is CheckStatus.WARNING and any(c.startswith("Result shape:") for c in result.caveats)


def test_a_retry_that_does_not_improve_keeps_the_first_result(candidate):
    worse = f"SELECT s.symbol, h.cost_basis {JOINS} ORDER BY h.quantity DESC LIMIT 3"
    result = candidate(Scripted(draft(BAD), draft(worse))).answer(RANK_Q)
    assert result.sql == BAD and "did not improve" in result.shape_policy.retry_failed


def test_an_unsafe_first_draft_is_rejected_and_never_retried(candidate):
    llm = Scripted(draft("DELETE FROM holdings"))
    result = candidate(llm).answer(RANK_Q)
    assert result.status == "rejected" and result.execution is None and len(llm.prompts) == 1


def test_at_most_one_retry_is_ever_sent(candidate):
    llm = Scripted(draft(BAD), draft(BAD))
    result = candidate(llm).answer(RANK_Q)
    assert len(llm.prompts) == 2 and result.llm_calls == 2


def test_a_wrong_figure_that_survives_the_retry_fails_verification(candidate):
    bad_share = share_with("ROUND(part.part_value * 50.0 / whole.total_value, 2)")
    result = candidate(Scripted(draft(bad_share), draft(bad_share))).answer(SHARE_Q)
    check = next(c for c in result.checks if c.name == "Result fits the question")
    assert check.status is CheckStatus.FAIL and result.verification is VerificationStatus.VERIFICATION_FAILED


def test_the_population_assumption_reaches_the_reader(candidate):
    result = candidate(Scripted(draft(SHARE_OK))).answer(SHARE_Q)
    assert any("did not ask for the others" in c for c in result.caveats)


def test_the_baseline_never_runs_the_policy(readonly_db):
    tool = SqlTool(readonly_db, SemanticContext.from_db(readonly_db), Scripted(draft(BAD)), variant=BASELINE_VARIANT)
    result = tool.answer(RANK_Q)
    assert result.shape_policy is None and result.llm_calls == 1 and result.sql == BAD


# --- real evidence: references as a positive control, saved model drafts as the real-output check ---------------------

def _saved_drafts(path_glob, field):
    latest = {}
    for path in sorted((PROJECT_ROOT / "reports").glob(path_glob)):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                latest[record["case"]["id"]] = record
    drafts = {}
    for case_id, record in latest.items():
        sql = (record.get(field) or {}).get("sql") if field == "result" else ((record.get("receipt") or {}).get("sql") or {}).get("sql")
        if sql:
            drafts[case_id] = (record["case"]["question"], sql)
    return drafts


def assessed(question, sql, schema, facts, db):
    result = execute_sql(db, sql, max_rows=500)
    return assess_result(assess(question, sql, schema, facts), result.columns, result.rows)


def test_no_reference_query_is_flagged(schema, facts, readonly_db):
    false_positives = {}
    for case in official.load_cases():
        if case.reference_sql:
            found = errors(assessed(case.question, case.reference_sql, schema, facts, readonly_db))
            if found:
                false_positives[case.id] = found
    for case in sql_eval.load_cases():
        if case.group != "official" and getattr(case, "reference_sql", None):
            found = errors(assessed(case.question, case.reference_sql, schema, facts, readonly_db))
            if found:
                false_positives[case.id] = found
    assert not false_positives


def test_the_saved_gemini_drafts_are_flagged_exactly_where_they_failed(schema, facts, readonly_db):
    drafts = _saved_drafts("runs/*/sql_outputs.jsonl", "result")
    flagged = {cid for cid, (q, sql) in drafts.items() if errors(assessed(q, sql, schema, facts, readonly_db))}
    assert flagged == {"Q5", "Q7", "U4"}


def test_the_saved_flash_lite_drafts_are_flagged_exactly_where_they_failed(schema, facts, readonly_db):
    drafts = _saved_drafts("supplemental/gemini-3.5-flash-lite/runs/*/official_outputs.jsonl", "receipt")
    flagged = {cid for cid, (q, sql) in drafts.items() if errors(assessed(q, sql, schema, facts, readonly_db))}
    assert flagged == {"Q4", "Q5", "Q7"}


def test_the_v21_live_drafts_are_flagged_only_where_v21_over_projected(schema, facts, readonly_db):
    drafts = _saved_drafts("sprint_v2_1/candidate/runs/*/sql_outputs.jsonl", "result")
    assert len(drafts) == 18
    flagged = {cid: errors(assessed(q, sql, schema, facts, readonly_db)) for cid, (q, sql) in drafts.items()}
    assert {cid: codes for cid, codes in flagged.items() if codes} == {"U5": {"over_projection"}, "U10": {"over_projection"}}


def test_each_saved_failure_is_flagged_for_its_actual_defect(schema, facts, readonly_db):
    drafts = _saved_drafts("runs/*/sql_outputs.jsonl", "result")
    q5 = errors(assessed(*drafts["Q5"], schema, facts, readonly_db))
    q7 = errors(assessed(*drafts["Q7"], schema, facts, readonly_db))
    assert q5 == {"missing_record_identity", "missing_identity_column"}
    assert q7 == {"missing_denominator_column", "population_includes_non_qualifying"}


@pytest.mark.parametrize("case_id", ["Q5", "Q7"])
def test_a_query_with_the_required_shape_passes_the_unchanged_official_comparator(case_id, schema, facts, readonly_db):
    """A pipeline check with written SQL, not evidence about the model: such a shape really does score PASS."""
    case = next(c for c in official.load_cases() if c.id == case_id)
    if case_id == "Q5":
        shaped = ("SELECT h.holding_id, s.symbol, s.company_name, h.quantity, h.cost_basis FROM holdings h "
                  "JOIN securities s ON s.security_id = h.security_id JOIN portfolios p ON p.portfolio_id = h.portfolio_id "
                  "WHERE p.portfolio_name = 'Growth Equity Fund' ORDER BY h.cost_basis DESC LIMIT 5")
    else:
        shaped = ("WITH part AS (SELECT h.portfolio_id, SUM(h.quantity * s.current_price) AS sector_value FROM holdings h "
                  "JOIN securities s ON s.security_id = h.security_id JOIN sectors sec ON sec.sector_id = s.sector_id "
                  "WHERE sec.sector_name = 'Technology' GROUP BY h.portfolio_id), "
                  "whole AS (SELECT h.portfolio_id, SUM(h.quantity * s.current_price) AS portfolio_value FROM holdings h "
                  "JOIN securities s ON s.security_id = h.security_id GROUP BY h.portfolio_id) "
                  "SELECT p.portfolio_name, part.sector_value, whole.portfolio_value, ROUND(part.sector_value * 100.0 / whole.portfolio_value, 2) AS percentage "
                  "FROM part JOIN whole ON whole.portfolio_id = part.portfolio_id JOIN portfolios p ON p.portfolio_id = part.portfolio_id ORDER BY percentage DESC")
    reference = execute_sql(readonly_db, case.reference_sql, max_rows=1000)
    result = execute_sql(readonly_db, shaped, max_rows=1000)
    assert compare_results(reference.columns, reference.rows, result.columns, result.rows, ordered=requires_order(case.reference_sql)).strict
    assert not errors(assessed(case.question, shaped, schema, facts, readonly_db))
