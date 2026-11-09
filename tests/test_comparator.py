"""Milestone 3: the execution-based comparator applies B7, B12 and B13 to realistic variants of Q1-Q8."""

import pytest

from portfolioiq_eval.comparator import compare_results, requires_order, values_equal

GROWTH_TOP5 = ("FROM holdings h JOIN securities s ON s.security_id = h.security_id "
               "JOIN portfolios p ON p.portfolio_id = h.portfolio_id WHERE p.portfolio_name = 'Growth Equity Fund'")
TECH_SHARE = ("SELECT p.portfolio_name, "
              "SUM(CASE WHEN sec.sector_name = 'Technology' THEN h.quantity * s.current_price END) AS tech_value, "
              "SUM(h.quantity * s.current_price) AS total_value, "
              "{scale}SUM(CASE WHEN sec.sector_name = 'Technology' THEN h.quantity * s.current_price END) "
              "/ SUM(h.quantity * s.current_price) AS tech_share "
              "FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id "
              "JOIN securities s ON s.security_id = h.security_id LEFT JOIN sectors sec ON sec.sector_id = s.sector_id "
              "GROUP BY p.portfolio_id HAVING tech_value IS NOT NULL")
DIVERSIFIED = ("SELECT p.portfolio_name, COUNT(DISTINCT CASE WHEN s.asset_type = 'Stock' THEN s.sector_id END) AS sectors, "
               "COUNT(*) AS holdings, 1.0 * COUNT(DISTINCT CASE WHEN s.asset_type = 'Stock' THEN s.sector_id END) / COUNT(*) "
               "AS ratio FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id "
               "JOIN securities s ON s.security_id = h.security_id GROUP BY p.portfolio_id HAVING sectors > {threshold}")

VARIANTS = [
    # (question id, candidate SQL, strict, relaxed, why)
    (1, "SELECT COUNT(portfolio_id) AS total_portfolios FROM portfolios", True, True, "alias differs"),
    (1, "SELECT COUNT(*) FROM portfolios WHERE status = 'Active'", False, False, "wrong filter"),
    (2, "SELECT portfolio_name AS name FROM portfolios WHERE status = 'Active' ORDER BY portfolio_name DESC",
     True, True, "order is free without LIMIT"),
    (2, "SELECT portfolio_name, status FROM portfolios WHERE status = 'Active'", False, True, "extra column"),
    (2, "SELECT portfolio_name FROM portfolios WHERE status = 'active'", False, False, "case-sensitive value"),
    (3, "SELECT s.company_name, s.symbol FROM securities s JOIN sectors x ON x.sector_id = s.sector_id "
        "WHERE x.sector_name = 'Technology'", True, True, "columns swapped"),
    (3, "SELECT s.symbol, s.company_name, s.current_price FROM securities s JOIN sectors x "
        "ON x.sector_id = s.sector_id WHERE x.sector_name = 'Technology'", False, True, "extra column"),
    (3, "SELECT s.symbol FROM securities s JOIN sectors x ON x.sector_id = s.sector_id "
        "WHERE x.sector_name = 'Technology'", False, False, "missing column"),
    (4, "SELECT TOTAL(total_aum) AS aum FROM portfolios WHERE target_risk_level = 'High'", True, True, "TOTAL vs SUM"),
    (4, "SELECT SUM(total_aum) FROM portfolios WHERE target_risk_level = 'high'", False, False, "NULL from wrong case"),
    (5, f"SELECT s.symbol, h.cost_basis, h.holding_id, s.company_name, h.quantity {GROWTH_TOP5} "
        "ORDER BY h.cost_basis DESC LIMIT 5", True, True, "columns reordered, rows in order"),
    (5, "SELECT t.* FROM (SELECT h.holding_id, s.symbol, s.company_name, h.quantity, h.cost_basis "
        f"{GROWTH_TOP5} ORDER BY h.cost_basis DESC LIMIT 5) AS t ORDER BY t.cost_basis ASC",
     False, False, "right rows, wrong order for a top-N question"),
    (6, "SELECT sec.sector_name, AVG(s.current_price) FROM securities s JOIN sectors sec "
        "ON sec.sector_id = s.sector_id GROUP BY sec.sector_name ORDER BY sec.sector_name", True, True, "other order"),
    (6, "SELECT sec.sector_name, AVG(s.current_price) FROM sectors sec LEFT JOIN securities s "
        "ON s.sector_id = sec.sector_id GROUP BY sec.sector_name", False, False, "10 rows, 3 of them empty"),
    (7, TECH_SHARE.format(scale="100.0 * "), True, True, "unrounded percentage within tolerance"),
    (7, TECH_SHARE.format(scale=""), False, False, "fraction, not percentage: no unit conversion"),
    (8, DIVERSIFIED.format(threshold=5), True, True, "different SQL, same result"),
    (8, DIVERSIFIED.format(threshold=3), False, False, "wrong threshold"),
]


@pytest.mark.parametrize("question_id, candidate_sql, strict, relaxed, why", VARIANTS,
                         ids=[f"Q{v[0]}-{v[4]}" for v in VARIANTS])
def test_variants_of_the_official_questions(db, reference_sql, question_id, candidate_sql, strict, relaxed, why):
    reference = db.execute(reference_sql[question_id])
    reference_columns = [d[0] for d in reference.description]
    reference_rows = reference.fetchall()
    candidate = db.execute(candidate_sql)
    candidate_columns = [d[0] for d in candidate.description]
    result = compare_results(reference_columns, reference_rows, candidate_columns, candidate.fetchall(),
                             ordered=requires_order(reference_sql[question_id]))
    assert (result.strict, result.relaxed) == (strict, relaxed), result.reason


def test_only_top_n_questions_are_order_sensitive(reference_sql):
    assert {qid for qid, sql in reference_sql.items() if requires_order(sql)} == {5}


@pytest.mark.parametrize("a, b, equal", [
    (13, 13.0, True), (13, 13.004, False), (85_000_000.0, 85_000_000, True),
    (84.67, 84.6658, True), (0.8467, 84.67, False), (0.5, 0.5004, True),
    (None, None, True), (None, 0, False), ("Technology", "Technology ", True), ("13", 13, False),
])
def test_value_equality_rules(a, b, equal):
    assert values_equal(a, b) is equal


def test_empty_results():
    assert compare_results(["a"], [], ["x", "y"], [], ordered=False).strict
    assert not compare_results(["a"], [], ["a"], [(1,)], ordered=False).relaxed
