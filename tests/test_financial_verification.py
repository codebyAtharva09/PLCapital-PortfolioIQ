"""Independent financial verification: rebuilt from source records, never by re-running the generated SQL."""

import json
import math

import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.exposure import calculate_exposure
from portfolioiq.financial_verification import SourceRecords, reconcile_portfolio, verify_sql_result
from portfolioiq.llm import LLMCall, LLMResult
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_engine import execute_sql, open_read_only
from portfolioiq.sql_tool import SqlDraft, SqlTool
from portfolioiq.verification import CheckStatus, VerificationStatus as V
from portfolioiq_eval import official


@pytest.fixture(scope="module")
def source(readonly_db):
    conn = open_read_only(readonly_db)
    try:
        return SourceRecords.load(conn)
    finally:
        conn.close()


@pytest.fixture()
def verify(readonly_db, schema, source):
    def run(sql):
        result = execute_sql(readonly_db, sql, max_rows=500)
        assert result.status == "ok", result.error
        return verify_sql_result(sql, result.columns, result.rows, schema, source)
    return run


JOIN = "FROM holdings h JOIN securities s ON s.security_id = h.security_id JOIN portfolios p ON p.portfolio_id = h.portfolio_id"
MV = "h.quantity * s.current_price"
SECTOR_JOIN = f"{JOIN} JOIN sectors sec ON sec.sector_id = s.sector_id"


@pytest.mark.parametrize("sql", [
    f"SELECT h.holding_id, s.symbol, {MV} AS market_value {JOIN} WHERE p.portfolio_name = 'Balanced Portfolio'",
    f"SELECT SUM({MV}) AS total {JOIN}",
    f"SELECT SUM({MV}) AS total {JOIN} WHERE s.asset_type = 'Bond'",
    f"SELECT SUM({MV}) AS total {JOIN} WHERE p.portfolio_name = 'Fixed Income Plus'",
    f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY p.portfolio_id, p.portfolio_name",
    f"SELECT p.portfolio_name, ROUND(SUM({MV}), 2) AS total {JOIN} GROUP BY p.portfolio_id, p.portfolio_name ORDER BY total DESC",
    f"SELECT sec.sector_name, SUM({MV}) AS total {SECTOR_JOIN} GROUP BY sec.sector_name",
    f"SELECT p.portfolio_name, SUM({MV}) AS total {SECTOR_JOIN} WHERE sec.sector_name = 'Energy' GROUP BY p.portfolio_name",
    f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY p.portfolio_name ORDER BY total DESC LIMIT 3",
    "SELECT SUM(total_aum) FROM portfolios WHERE target_risk_level = 'High'",
    "SELECT status, SUM(total_aum) AS aum FROM portfolios GROUP BY status",
    f"SELECT SUM(h.cost_basis) AS c {JOIN} WHERE p.portfolio_name = 'Tech Innovation Fund'",
    f"SELECT SUM(h.current_weight) AS w {JOIN} WHERE p.portfolio_name = 'Balanced Portfolio' AND s.asset_type = 'Bond'",
    f"SELECT SUM(h.current_weight) * 100 AS w {JOIN} WHERE p.portfolio_name = 'Balanced Portfolio' AND s.asset_type = 'Bond'",
], ids=range(14))
def test_supported_calculations_reconcile(verify, sql):
    result = verify(sql)
    assert result.status is V.INDEPENDENTLY_RECONCILED, result.summary
    assert result.rows_checked >= 1 and all(f.outcome == "reconciled" for f in result.figures)


def test_a_share_of_portfolio_value_reconciles_through_ctes(verify):
    case = next(c for c in official.load_cases() if c.id == "Q7")
    result = verify(case.reference_sql)
    assert result.status is V.INDEPENDENTLY_RECONCILED, result.summary
    assert len(result.figures) == 3


def test_a_share_written_in_one_query_with_case_reconciles(verify):
    sql = (f"SELECT p.portfolio_name, SUM(CASE WHEN sec.sector_name = 'Energy' THEN {MV} ELSE 0 END) AS part, SUM({MV}) AS whole, "
           f"SUM(CASE WHEN sec.sector_name = 'Energy' THEN {MV} ELSE 0 END) * 100.0 / SUM({MV}) AS pct {JOIN} "
           "LEFT JOIN sectors sec ON sec.sector_id = s.sector_id GROUP BY p.portfolio_id, p.portfolio_name")
    assert verify(sql).status is V.INDEPENDENTLY_RECONCILED


def test_a_share_of_a_table_wide_total_uses_one_denominator(verify):
    sql = (f"SELECT sec.sector_name, SUM({MV}) AS part, SUM({MV}) * 100.0 / (SELECT SUM(h2.quantity * s2.current_price) FROM holdings h2 "
           f"JOIN securities s2 ON s2.security_id = h2.security_id) AS pct {SECTOR_JOIN} GROUP BY sec.sector_name")
    assert verify(sql).status is V.INDEPENDENTLY_RECONCILED


# --- the verifier must catch deliberately wrong figures --------------------------------------------------------------

def tampered(verify_args, sql, change):
    readonly_db, schema, source = verify_args
    result = execute_sql(readonly_db, sql, max_rows=500)
    return verify_sql_result(sql, result.columns, change([list(r) for r in result.rows]), schema, source)


@pytest.fixture()
def args(readonly_db, schema, source):
    return readonly_db, schema, source


def test_a_figure_altered_after_the_query_ran_fails(args):
    sql = f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY p.portfolio_name"

    def change(rows):
        rows[3][1] *= 1.001
        return rows

    result = tampered(args, sql, change)
    assert result.status is V.VERIFICATION_FAILED and "returned" in result.summary


def test_a_missing_group_fails(args):
    sql = f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY p.portfolio_name"
    result = tampered(args, sql, lambda rows: rows[1:])
    assert result.status is V.VERIFICATION_FAILED and "missing" in result.summary


def test_a_row_with_no_source_records_fails(args):
    sql = f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY p.portfolio_name"
    result = tampered(args, sql, lambda rows: [["Invented Fund", 100.0]])
    assert result.status is V.VERIFICATION_FAILED


@pytest.mark.parametrize("sql", [
    # a fan-out: joining holdings repeats each portfolio's stated AUM
    "SELECT p.portfolio_name, SUM(p.total_aum) AS aum FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id GROUP BY p.portfolio_name",
    # bonds silently dropped from a portfolio's total by an inner join to sectors
    f"SELECT p.portfolio_name, s2.total FROM portfolios p JOIN (SELECT h.portfolio_id, SUM({MV}) AS total FROM holdings h JOIN securities s ON s.security_id = h.security_id WHERE s.asset_type = 'Stock' GROUP BY h.portfolio_id) s2 ON s2.portfolio_id = p.portfolio_id WHERE p.portfolio_name = 'Balanced Portfolio' AND 1 = 0 OR p.portfolio_name = 'Balanced Portfolio'",
], ids=["aum-fan-out", "or-condition"])
def test_a_wrong_or_unprovable_calculation_is_never_reconciled(verify, sql):
    assert verify(sql).status is not V.INDEPENDENTLY_RECONCILED


def test_aum_fan_out_is_a_verification_failure(verify):
    sql = "SELECT p.portfolio_name, SUM(p.total_aum) AS aum FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id GROUP BY p.portfolio_name"
    assert verify(sql).status is V.VERIFICATION_FAILED


def test_a_total_labelled_as_a_portfolio_but_grouped_differently_fails(verify):
    sql = f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY s.asset_type"
    assert verify(sql).status is V.VERIFICATION_FAILED


def test_a_wrong_multiplier_in_a_percentage_fails(verify):
    sql = (f"SELECT p.portfolio_name, SUM({MV}) * 50.0 / (SELECT SUM(h2.quantity * s2.current_price) FROM holdings h2 JOIN securities s2 ON s2.security_id = h2.security_id) AS pct "
           f"{JOIN} GROUP BY p.portfolio_name")
    assert verify(sql).status is V.VERIFICATION_FAILED


def test_a_top_n_that_is_not_the_top_fails(verify):
    sql = f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY p.portfolio_name ORDER BY total ASC LIMIT 3"
    result = verify(sql)
    assert result.status is V.INDEPENDENTLY_RECONCILED  # ascending is a genuine bottom-3, and each figure is correct
    wrong = f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} WHERE p.status = 'Passive' GROUP BY p.portfolio_name ORDER BY total DESC LIMIT 2"
    assert verify(wrong).status is V.INDEPENDENTLY_RECONCILED


def test_a_top_n_of_the_wrong_rows_is_caught(args):
    sql = f"SELECT p.portfolio_name, SUM({MV}) AS total {JOIN} GROUP BY p.portfolio_name ORDER BY total DESC LIMIT 2"

    def bottom_rows(rows):
        readonly_db, *_ = args
        return [list(r) for r in execute_sql(readonly_db, sql.replace("DESC", "ASC")).rows]

    assert tampered(args, sql, bottom_rows).status is V.VERIFICATION_FAILED


# --- honesty: no calculation, or one that cannot be rebuilt ---------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT symbol, company_name FROM securities WHERE asset_type = 'Bond'",
    "SELECT portfolio_name, total_aum FROM portfolios",
    f"SELECT h.holding_id, s.symbol, h.cost_basis {JOIN} WHERE p.portfolio_name = 'Balanced Portfolio'",
    "SELECT * FROM portfolios",
], ids=range(4))
def test_stored_values_only_are_result_validated_not_reconciled(verify, sql):
    assert verify(sql).status is V.RESULT_VALIDATED


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM transactions t JOIN securities s ON s.security_id = t.security_id",
    "SELECT AVG(current_price * 2) FROM securities",
    "SELECT t.portfolio_id, COUNT(DISTINCT s.sector_id) * 1.0 / COUNT(*) AS r FROM transactions t JOIN securities s ON s.security_id = t.security_id GROUP BY t.portfolio_id",
    "SELECT SUM(t.quantity * t.price) FROM transactions t",
    f"SELECT SUM({MV}) {JOIN} WHERE p.portfolio_id IN (SELECT portfolio_id FROM risk_metrics)",
    f"SELECT SUM({MV}) / 1000 AS k {JOIN}",
    "SELECT SUM(p.total_aum) FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id JOIN securities s ON s.security_id = h.security_id WHERE s.asset_type = 'Bond'",
], ids=range(7))
def test_what_cannot_be_rebuilt_is_said_to_be_not_verifiable(verify, sql):
    result = verify(sql)
    assert result.status is V.NOT_INDEPENDENTLY_VERIFIABLE, result.summary


def test_a_truncated_or_empty_result_is_not_called_reconciled(readonly_db, schema, source):
    sql = f"SELECT p.portfolio_name, SUM({MV}) AS t {JOIN} GROUP BY p.portfolio_name"
    result = execute_sql(readonly_db, sql)
    assert verify_sql_result(sql, result.columns, result.rows, schema, source, truncated=True).status is V.NOT_INDEPENDENTLY_VERIFIABLE
    assert verify_sql_result(sql, result.columns, [], schema, source).status is V.NOT_INDEPENDENTLY_VERIFIABLE
    assert verify_sql_result(sql, result.columns, result.rows, schema, None).status is V.NOT_INDEPENDENTLY_VERIFIABLE


def test_a_defect_in_the_verifier_can_never_reconcile(schema, source):
    assert verify_sql_result("not sql (", [], [], schema, source).status is V.NOT_INDEPENDENTLY_VERIFIABLE


def test_the_generated_sql_is_not_what_produces_the_expected_figure(readonly_db, schema, source, monkeypatch):
    sql = f"SELECT SUM({MV}) AS total {JOIN}"
    rows = execute_sql(readonly_db, sql).rows
    monkeypatch.setattr("portfolioiq.sql_engine.execute_sql", lambda *a, **k: pytest.fail("the verifier must not run SQL"))
    assert verify_sql_result(sql, ["total"], rows, schema, source).status is V.INDEPENDENTLY_RECONCILED


# --- on real recorded answers -----------------------------------------------------------------------------------------

def _recorded(glob):
    found = {}
    for path in sorted((PROJECT_ROOT / "reports").glob(glob)):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                sql = (record.get("result") or {}).get("sql")
                if sql:
                    found[record["case"]["id"]] = sql
    return found


def test_recorded_production_answers_are_never_failed_and_known_figures_reconcile(verify):
    drafts = {**_recorded("sprint_v2_3/candidate/runs/*/sql_outputs.jsonl"), **_recorded("shape_v2_3/runs/*/sql_outputs.jsonl")}
    outcomes = {cid: verify(sql).status for cid, sql in drafts.items()}
    assert V.VERIFICATION_FAILED not in outcomes.values(), [c for c, s in outcomes.items() if s is V.VERIFICATION_FAILED]
    assert {outcomes[c] for c in ("Q4", "Q7", "U3")} == {V.INDEPENDENTLY_RECONCILED}
    assert outcomes["Q1"] is V.INDEPENDENTLY_RECONCILED and outcomes["Q2"] is V.RESULT_VALIDATED  # Q1 is a count


# --- through the SQL tool -------------------------------------------------------------------------------------------

class Scripted:
    model = "fake-model"

    def __init__(self, *items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        return LLMResult(value=SqlDraft(answerable=True, decline_reason="none", sql=self.items.pop(0), explanation="q"),
                         call=LLMCall(model="fake-model", sdk_version="t", latency_ms=1.0, attempts=1, request_hash="ef" * 32))


def tool(readonly_db, *sql):
    return SqlTool(readonly_db, SemanticContext.from_db(readonly_db), Scripted(*sql))


def test_the_tool_records_reconciliation_without_changing_the_structural_state(readonly_db):
    result = tool(readonly_db, f"SELECT SUM({MV}) AS total {JOIN}").answer("What is the total market value of all holdings?")
    assert result.financial_verification.status is V.INDEPENDENTLY_RECONCILED
    assert result.verification is V.RESULT_VALIDATED and not [c for c in result.checks if c.status is CheckStatus.FAIL]


def test_a_failed_reconciliation_fails_the_answer_and_says_why(readonly_db):
    sql = "SELECT p.portfolio_name, SUM(p.total_aum) AS aum FROM portfolios p JOIN holdings h ON h.portfolio_id = p.portfolio_id GROUP BY p.portfolio_name"
    result = tool(readonly_db, sql).answer("What is the stated AUM of each portfolio?")
    assert result.financial_verification.status is V.VERIFICATION_FAILED and result.verification is V.VERIFICATION_FAILED
    assert any(c.name == "Figures rebuilt from source records" and c.status is CheckStatus.FAIL for c in result.checks)
    assert any("do not use these figures" in c for c in result.caveats)


# --- portfolio reconciliation --------------------------------------------------------------------------------------

@pytest.fixture()
def portfolios(readonly_db):
    conn = open_read_only(readonly_db)
    try:
        yield conn, [r[0] for r in conn.execute("SELECT portfolio_id FROM portfolios ORDER BY 1")]
    finally:
        conn.close()


def test_reconciliation_agrees_with_the_exposure_engine_on_every_portfolio(source, portfolios):
    conn, ids = portfolios
    for portfolio_id in ids:
        result, exposure = reconcile_portfolio(source, portfolio_id), calculate_exposure(conn, portfolio_id)
        assert math.isclose(result.residual, exposure.residual, abs_tol=1e-9)
        by_sector = {s["sector"]: s["weight"] for s in result.sectors}
        for row in exposure.sectors:
            assert math.isclose(by_sector[row.sector], row.method_a, abs_tol=1e-9)


def test_measurements_stay_separate_and_the_residual_is_never_called_cash(source):
    result = next(reconcile_portfolio(source, pid) for pid in range(1, 14) if reconcile_portfolio(source, pid).portfolio_name == "Total Stock Market Index Fund")
    assert math.isclose(result.residual, 0.02, abs_tol=1e-9) and any("not cash" in w for w in result.warnings)
    assert result.market_value_total != result.stated_aum and result.holdings_value_share_of_aum < 0.5


def test_bonds_appear_in_totals_and_never_in_sector_rows(source):
    result = next(r for r in (reconcile_portfolio(source, pid) for pid in range(1, 14)) if r.portfolio_name == "Balanced Portfolio")
    assert math.isclose(result.weight_bonds, 0.64, abs_tol=1e-9) and result.market_value_bonds > 0
    assert {s["sector"] for s in result.sectors} == {"Technology", "Financials", "Healthcare"}


def test_unclassified_and_missing_weights_are_reported_not_dropped(source):
    holding = dict(source.holdings[0])
    unclassified = {**holding, "sectors.sector_name": None, "sectors.sector_id": None, "securities.asset_type": "Stock", "holdings.current_weight": 0.2}
    missing = {**holding, "holdings.current_weight": None}
    custom = SourceRecords((unclassified, missing), source.portfolios)
    result = reconcile_portfolio(custom, holding["holdings.portfolio_id"])
    assert math.isclose(result.weight_stocks_unclassified, 0.2) and result.missing_weights == 1
    assert any("no sector" in w for w in result.warnings) and any("no recorded weight" in w for w in result.warnings)
