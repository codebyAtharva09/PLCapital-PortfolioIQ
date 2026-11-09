"""Streamlit app tests: every page renders headlessly with no exception and no Gemini request.

AppTest runs the real page code in-process. The API key is blanked, the live Gemini client is replaced by
one that fails if constructed, and the usage ledger is checked to be byte-identical afterwards. Fakes appear
only here: the app itself never shows a mocked result.
"""

import json
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import portfolioiq.agent as agent_module
from portfolioiq.config import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
LEDGER = PROJECT_ROOT / "reports" / "api_usage.jsonl"
VIEW = """
import sys
sys.path.insert(0, {app!r})
import streamlit as st
import ui
ui.inject_css()
{setup}
from views import {view}
{view}.render()
"""
PAGES = ["overview", "ask", "sql_intelligence", "exposure", "evaluation", "methodology", "setup"]
SECRET_MARKERS = ("Traceback", "AIza", "Authorization:")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_live_client(*args, **kwargs):
        raise AssertionError("the app must not create a live Gemini client in these tests")

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(agent_module, "GeminiLLM", no_live_client)
    for name in [m for m in sys.modules if m in ("backend", "ui", "evidence") or m == "views" or m.startswith("views.")]:
        del sys.modules[name]  # a fake installed by one test must not reach the next
    st.cache_data.clear()
    st.cache_resource.clear()
    before = LEDGER.read_bytes() if LEDGER.exists() else None
    yield
    assert (LEDGER.read_bytes() if LEDGER.exists() else None) == before  # no request was logged


def view(name, setup=""):
    return AppTest.from_string(VIEW.format(app=str(APP), view=name, setup=setup), default_timeout=120)


def html_text(at):
    return "\n".join(str(h.value) for h in at.html)


def ask(at, question, key="ask_question"):
    at.text_area(key=key).input(question)
    return at.button[0].click().run()


@pytest.mark.parametrize("name", PAGES)
def test_every_page_renders_without_errors(name):
    at = view(name).run()
    assert not at.exception, [e.value for e in at.exception]
    text = html_text(at)
    assert "piq-title" in text and not any(marker in text for marker in SECRET_MARKERS)


def test_full_app_opens_on_the_overview_with_the_context_strip():
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=120).run()
    assert not at.exception
    text = html_text(at)
    assert "Data as of" in text and "2023-10-09" in text and "no Gemini key: demo replay only" in text
    assert "Try PortfolioIQ in 5 minutes" in text


def test_navigation_lists_the_seven_pages_in_order():
    source = (APP / "streamlit_app.py").read_text(encoding="utf-8")
    titles = ["Overview", "Ask PortfolioIQ", "SQL Intelligence", "Exposure Analysis", "Evaluation & Evidence", "Data & Methodology", "CLI & Setup"]
    positions = [source.index(f'title="{t}"') for t in titles]
    assert positions == sorted(positions) and 'position="sidebar"' in source


def test_quick_test_steps_prefill_a_genuine_example():
    at = view("overview").run()
    at.button(key="step_ask").click().run()
    assert at.session_state["ask_draft"] == "Which high-risk portfolio has the largest Technology exposure?"
    at.button(key="step_exposure").click().run()
    assert at.session_state["portfolio"] == "Balanced Portfolio"


def test_overview_states_the_product_and_real_dataset_figures():
    text = html_text(view("overview").run())
    assert "AI-Powered Portfolio Analytics Agent" in text and "Natural Language to SQL" in text
    assert "Intent router" in text and "Answer and receipt" in text
    assert "Data snapshot date: 2023-10-09" in text and "95" in text
    for not_a_dependency in ("Plotly", "NumPy"):
        assert not_a_dependency not in text


def test_overview_reports_the_official_result_with_its_failures():
    text = html_text(view("overview").run())
    official = json.loads((PROJECT_ROOT / "reports" / "sprint_v2_3" / "candidate" / "official_evaluation.json").read_text(encoding="utf-8"))
    agent = official["summary"]["agent"]
    assert f"{agent['PASS']} of {agent['total']}" in text and "original baseline 8 of 10" in text
    if agent["FAIL"]:
        assert "FAIL:" in text


def test_exposure_shows_both_methods_denominators_and_warns_about_bonds_before_the_figures():
    at = view("exposure", setup='st.session_state["portfolio"] = "Balanced Portfolio"').run()
    text = html_text(at)
    assert "Bonds (excluded)" in text and "61.11" in text and "Independently reconciled" in text
    assert "Method A: portfolio-wide exposure" in text and "Method B: equity-sleeve exposure" in text
    assert "Whole portfolio (100% of portfolio weight)" in text and "Eligible stock weight (36.00% of portfolio)" in text
    assert text.index("Bonds excluded") < text.index("Results")


def test_reconciliation_keeps_each_measurement_on_its_own_basis():
    text = html_text(view("exposure", setup='st.session_state["portfolio"] = "Balanced Portfolio"').run())
    assert "Four measurements, four bases" in text and "Stated AUM (USD)" in text and "Holdings market value (USD)" in text
    assert "not cash" in text.lower() and "Where the weight sits" in text


def test_exposure_shows_the_residual_separately_and_never_as_cash():
    text = html_text(view("exposure", setup='st.session_state["portfolio"] = "Total Stock Market Index Fund"').run())
    assert "Unattributed weight residual" in text and "2.00" in text and "not cash" in text.lower()


def test_exposure_for_a_bond_only_portfolio_shows_an_empty_state_not_figures():
    at = view("exposure", setup='st.session_state["portfolio"] = "Fixed Income Plus"').run()
    text = html_text(at)
    assert not at.exception and "No eligible stocks" in text and "Method A: portfolio-wide" not in text


EXPOSURE_FAKE = """
import backend
from portfolioiq.exposure import ExposureResult, Reconciliation
from portfolioiq.verification import Check, CheckStatus, VerificationStatus
checks = [Check(name="Weights present", status=CheckStatus.PASS, detail="ok"),
          Check(name="Weights valid (0 to 1)", status=CheckStatus.FAIL, detail="Weights outside 0 to 1: AAPL (1.5)."),
          Check(name="No duplicate holdings", status=CheckStatus.PASS, detail="ok")]
fake = ExposureResult(portfolio_id=1, portfolio_name="Test", status="invalid_data", message="Test has weights outside 0 to 1.",
    method_a_denominator="d", method_b_denominator="Not applicable", eligible_stock_weight=0.0, sectors=[], excluded=[], total_weight=1.5,
    residual=-0.5, residual_reported=False, checks=checks,
    reconciliation=Reconciliation(status=CheckStatus.NOT_APPLICABLE, max_abs_difference=None, detail="n"),
    verification=VerificationStatus.VERIFICATION_FAILED, caveats=[])
backend.exposure = lambda portfolio_id: fake
"""


def test_exposure_shows_the_rejection_for_invalid_weights():
    text = html_text(view("exposure", setup=EXPOSURE_FAKE).run())
    assert "Exposure not calculated" in text and "AAPL (1.5)" in text and "Verification failed" in text
    assert "Method A: portfolio-wide" not in text


def test_data_methodology_lists_checks_valuation_and_limitations():
    text = html_text(view("methodology").run())
    assert "22 checks" in text and "I06" in text and "0 FAIL" in text
    assert "Holdings market value" in text and "Portfolio AUM" in text and "Current-weight allocation" in text
    assert "No geographic diversification claim" in text and "portfolios 1 to 4" in text
    assert "is never called AUM" in text and "1.68% to 14.72%" in text


def test_limitations_list_untested_cases_from_the_reports():
    text = html_text(view("methodology").run())
    sql = json.loads((PROJECT_ROOT / "reports" / "sprint_v2_3" / "candidate" / "sql_evaluation.json").read_text(encoding="utf-8"))
    for item in sql["items"]:
        if item["verdict"] == "NOT EVALUATED":
            assert item["id"] in text


def test_evaluation_separates_the_four_sections_and_keeps_failures_visible():
    at = view("evaluation").run()
    text = html_text(at)
    assert {t.label for t in at.tabs} >= {"Production Evaluation", "Original Baseline", "Candidate History", "Supplemental Evaluation", "Security Evaluation"}
    assert "Q5" in text and "Q7" in text and "FAIL" in text and "Not official results" in text
    assert "Candidate results are separate" in text and "gemini-3.5-flash-lite" in text
    assert "d5a914eb71d5" in text and "314e91c11435" in text and "Dataset fingerprint" in text
    assert "NOT YET EVALUATED" in text


def test_candidate_section_says_so_when_none_is_recorded():
    at = view("evaluation", setup="import backend\nbackend.candidate_runs = lambda: []\nbackend.diagnostic_comparisons = lambda: []").run()
    assert "No live candidate evaluation recorded" in html_text(at)


def test_evaluation_has_no_live_run_button():
    at = view("evaluation").run()
    labels = [b.label.lower() for b in at.button]
    assert labels == ["re-score recorded run now"] and "cannot be started by accident" in html_text(at)


def test_offline_rescore_reproduces_the_saved_result_without_writing_reports():
    report = PROJECT_ROOT / "reports" / "official_evaluation.json"
    before = report.read_bytes()
    at = view("evaluation").run()
    at.button(key="rescore").click().run()
    assert not at.exception, [e.value for e in at.exception]
    text = html_text(at)
    assert "Reproduced" in text and "No request was sent" in text and report.read_bytes() == before


def test_evaluation_empty_state_when_no_reports_exist():
    at = view("evaluation", setup="import backend\nbackend.report = lambda name: None").run()
    assert not at.exception and "No evaluation run yet" in html_text(at)


def test_evaluation_says_when_no_test_run_is_recorded():
    setup = """import backend
real = backend.report
backend.report = lambda name: None if name == "tests" else real(name)
"""
    assert "No recorded test run" in html_text(view("evaluation", setup=setup).run())


def test_evaluation_labels_test_cases_separately_from_live_questions():
    setup = """import backend
real = backend.report
fake = {"recorded_utc": "2026-11-09T00:00:00+00:00", "python": "3.13.4", "total": 3, "passed": 3, "failed": 0, "skipped": 0,
        "categories": [{"category": "Unit", "passed": 3, "failed": 0, "skipped": 0, "files": 1}]}
backend.report = lambda name: fake if name == "tests" else real(name)
"""
    text = html_text(view("evaluation", setup=setup).run())
    assert "Test cases passed" in text and "Live questions answered by Gemini" in text and "They are not live questions" in text


def test_ask_demo_mode_replays_a_recorded_question_and_says_so():
    at = ask(view("ask").run(), "How many holdings does the Dividend Aristocrats Fund have?")
    assert not at.exception, [e.value for e in at.exception]
    text = html_text(at)
    assert "DEMO / FIXTURE MODE" in text and "holding_count: 4" in text and "Not independently verifiable" in text
    assert "Selected: SQL" in text


def test_ask_shows_the_evidence_panel_with_receipt_fields():
    at = ask(view("ask").run(), "How many holdings does the Dividend Aristocrats Fund have?")
    assert [e.label for e in at.expander if e.label == "View Calculation & Evidence"]
    text = html_text(at)
    for field in ("Dataset fingerprint", "SQL prompt version", "Router version", "Latency", "Allowed tables", "Read-only SELECT", "SQL parameters: none"):
        assert field in text
    assert any("SELECT" in c.value for c in at.code)


def test_ask_exposure_answer_shows_calculation_but_runs_no_sql():
    at = ask(view("ask").run(), "What are the sector exposures for the Tech Innovation Fund?")
    text = html_text(at)
    assert "Selected: Exposure" in text and "Method A denominator" in text and "Independently reconciled" in text
    assert "SQL Query Tool" not in text and "Generated SQL and safety" not in text


def test_ask_hybrid_answer_shows_ordered_steps():
    at = ask(view("ask").run(), "Which high-risk portfolio has the largest Technology exposure?")
    text = html_text(at)
    assert "Selected: Hybrid" in text and "1. Intent router" in text and "2. SQL Query Tool" in text and "3. Exposure calculator" in text


def test_ask_clarification_offers_candidates():
    at = ask(view("ask").run(), "Sector exposure for the international portfolio")
    assert "Choose a portfolio" in html_text(at) and any("International Equity Fund" in b.label for b in at.button)


def test_ask_demo_mode_never_invents_an_answer_for_an_unrecorded_question():
    text = html_text(ask(view("ask").run(), "What is the weather in Mumbai?"))
    assert "No recorded Gemini response matches this question" in text and "nothing was invented" in text


def test_a_second_submit_while_running_is_not_sent_twice():
    at = view("ask").run()
    at = ask(at, "How many holdings does the Dividend Aristocrats Fund have?")
    at = at.button[0].click().run()
    assert len(at.session_state["ask_history"]) == 1


def test_rendering_again_does_not_re_ask():
    at = ask(view("ask").run(), "How many holdings does the Dividend Aristocrats Fund have?")
    first = at.session_state["ask_history"][0].receipt_id
    at = at.run()
    assert len(at.session_state["ask_history"]) == 1 and at.session_state["ask_history"][0].receipt_id == first


def test_live_mode_without_a_key_is_disabled_with_an_explanation():
    at = view("ask", setup='st.session_state["ask_mode"] = "Live Gemini"').run()
    assert "Live mode unavailable" in html_text(at) and at.button[0].disabled


@pytest.mark.parametrize("kind, label", [("quota_exhausted", "Gemini daily quota used up"), ("unavailable", "Gemini is temporarily unavailable"),
                                         ("malformed_output", "Invalid model response"), ("network", "Gemini could not be reached")])
def test_gemini_failures_show_guidance_and_never_the_raw_provider_message(kind, label):
    raw = "Gemini API error 429 RESOURCE_EXHAUSTED: quota_project=secret-project-123"
    setup = f"""
import backend
from portfolioiq.llm import LLMError
class Failing:
    model = "m"
    def generate(self, **kwargs):
        raise LLMError({kind!r}, {raw!r}, attempts=1)
from portfolioiq.agent import PortfolioAgent
agent = PortfolioAgent.from_settings(backend.settings(), llm=Failing())
backend.demo_agent = lambda: agent
"""
    at = ask(view("ask", setup=setup).run(), "How many portfolios do we have?")
    text = html_text(at)
    assert not at.exception and label in text and "secret-project-123" not in text


def test_an_unexpected_failure_shows_a_plain_message_and_no_stack_trace():
    setup = """
import backend
class Broken:
    def ask(self, *a, **k):
        raise RuntimeError("database path C:/secret/location is locked")
backend.demo_agent = lambda: Broken()
"""
    at = ask(view("ask", setup=setup).run(), "How many portfolios do we have?")
    text = html_text(at)
    assert not at.exception and "Something went wrong" in text and "secret/location" not in text


def test_sql_page_shows_sql_guard_result_and_rows():
    at = ask(view("sql_intelligence").run(), "Which securities are in the Technology sector?", key="sql_question")
    assert not at.exception, [e.value for e in at.exception]
    text = html_text(at)
    for field in ("B. Generated SQL", "C. Safety validation", "D. Query result", "E. Query details", "Allowed tables", "Allowed columns",
                  "Allowed and executed", "2 seconds", "500 rows"):
        assert field in text
    assert any(c.value.lstrip().upper().startswith(("SELECT", "WITH")) for c in at.code) and len(at.dataframe) >= 1


def test_sql_page_shows_a_refusal_without_claiming_validation():
    at = ask(view("sql_intelligence").run(), "Delete all transactions before October 2023.", key="sql_question")
    text = html_text(at)
    assert "Rejected" in text and "The SQL guard did not run" in text and "Allowed and executed" not in text


def test_sql_page_never_offers_a_raw_sql_input():
    at = view("sql_intelligence").run()
    labels = [t.label.lower() for t in at.text_area]
    assert labels == ["database question"]


def test_guard_bench_shows_real_rejections_and_marks_later_stages_not_reached():
    at = view("sql_intelligence").run()
    at.selectbox(key="guard_case").select("System table").run()
    text = html_text(at)
    assert "Rejected by the guard" in text and "forbidden_table" in text and "Not reached" in text


def test_sql_page_reports_a_gemini_failure_without_showing_a_query():
    setup = """
import backend
from portfolioiq.llm import LLMError
from portfolioiq.sql_tool import SqlTool
class Failing:
    model = "m"
    def generate(self, **kwargs):
        raise LLMError("quota_exhausted", "raw provider text", attempts=1)
tool = SqlTool.from_settings(backend.settings(), llm=Failing())
backend.demo_sql_tool = lambda: tool
"""
    at = ask(view("sql_intelligence", setup=setup).run(), "How many portfolios?", key="sql_question")
    text = html_text(at)
    assert "No SQL was generated" in text and "raw provider text" not in text and not at.dataframe


def test_setup_shows_real_cli_output_and_no_secret_values():
    at = view("setup").run()
    assert not at.exception, [e.value for e in at.exception]
    code = "\n".join(c.value for c in at.code)
    assert "python -m portfolioiq exposure" in code and "Technology" in code and "DEMO / FIXTURE MODE" in code
    assert "GEMINI_API_KEY" in html_text(at) and "AIza" not in code


def test_table_cells_are_escaped():
    at = AppTest.from_string(f"""
import sys
sys.path.insert(0, {str(APP)!r})
import ui
ui.table(["Name"], [["<script>alert(1)</script>"]])
""", default_timeout=30).run()
    text = html_text(at)
    assert "&lt;script&gt;" in text and "<script>" not in text


def test_cli_and_streamlit_resolve_the_same_active_configuration():
    from portfolioiq.cli import build_sql_tool

    tool = build_sql_tool(replay=True)
    assert (tool.variant, tool.prompt_version) == ("candidate-2.3", "314e91c11435")
    text = html_text(AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=120).run())
    assert f"SQL configuration {tool.variant} ({tool.prompt_version})" in text
