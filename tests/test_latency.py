"""Latency standard: every duration is measured on the monotonic clock and reported in milliseconds.

A stage that did not run is null, never 0 ms; the total is measured on its own clock and is not the sum of
the stages nested inside it; historical values are read as recorded and never rescaled.
"""

import itertools
import json
import re

import pytest

from portfolioiq import cli, timing
from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import PROJECT_ROOT
from portfolioiq.llm import LLMCall, LLMResult
from portfolioiq.receipt import FinancialAnswerReceipt, ModelUsage, Timings
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_tool import SqlDraft, SqlToolResult

PACKAGE = PROJECT_ROOT / "src" / "portfolioiq"


# --- conversion and validation ----------------------------------------------------------------

def test_one_second_is_1000_ms():
    assert timing.ms_from_seconds(1) == 1000.0


def test_a_value_already_in_milliseconds_is_not_rescaled():
    assert timing.round_ms(250.0) == 250.0
    assert timing.sum_measured([250.0]) == 250.0
    assert timing.format_ms(250.0) == "250 ms"


def test_sub_millisecond_intervals_keep_their_resolution():
    assert timing.ms_from_ns(412_000) == pytest.approx(0.412)
    assert timing.elapsed_ms(1_000, 1_750_000 + 1_000) == pytest.approx(1.75)
    assert timing.format_ms(0.412) == "0.412 ms"


def test_the_clock_is_monotonic_and_measures_real_elapsed_time():
    start = timing.now_ns()
    assert isinstance(start, int)
    assert timing.elapsed_ms(start) >= 0


@pytest.mark.parametrize("bad", [-1, -0.001, float("nan"), float("inf")])
def test_impossible_durations_are_instrumentation_errors(bad):
    with pytest.raises(timing.InstrumentationError):
        timing.ms_from_seconds(bad)
    with pytest.raises(timing.InstrumentationError):
        timing.format_ms(bad)


def test_end_before_start_is_rejected():
    with pytest.raises(timing.InstrumentationError):
        timing.elapsed_ms(5_000_000, 1_000_000)


# --- missing measurements stay missing --------------------------------------------------------

def test_an_unmeasured_stage_is_null_and_displayed_as_not_available():
    assert timing.sum_measured([]) is None and timing.sum_measured([None, None]) is None
    assert timing.sum_measured([None, 2.0]) == 2.0
    assert timing.format_ms(None) == "n/a"
    assert timing.round_ms(None) is None
    assert timing.format_stages([("Total", 5.0), ("SQL execution", None)]) == "Total 5.00 ms"


def test_a_new_receipt_has_no_invented_stage_values():
    t = Timings()
    assert all(getattr(t, name) is None for name in Timings.model_fields if name.endswith("_ms"))
    assert ModelUsage().llm_latency_ms is None


# --- display ----------------------------------------------------------------------------------

@pytest.mark.parametrize("value, shown", [(245, "245 ms"), (1248, "1,248 ms"), (12.45, "12.45 ms"), (800, "800 ms"),
                                          (0.8, "0.800 ms"), (99.999, "100.00 ms"), (100, "100 ms")])
def test_milliseconds_are_the_displayed_unit(value, shown):
    assert timing.format_ms(value) == shown
    assert "second" not in shown and not shown.endswith(" s")


# --- statistics -------------------------------------------------------------------------------

def test_summary_reports_min_median_mean_and_counts_unavailable_requests_apart():
    s = timing.summarise_ms([100.0, 300.0, None, 200.0], unavailable=2)
    assert (s["count"], s["unavailable"]) == (3, 2)
    assert (s["min_ms"], s["median_ms"], s["mean_ms"], s["max_ms"]) == (100.0, 200.0, 200.0, 300.0)
    assert s["p95_ms"] is None  # three observations are too few for a 95th percentile


def test_p95_appears_only_with_a_sufficient_sample():
    assert timing.summarise_ms(range(1, 20))["p95_ms"] is None
    assert timing.summarise_ms(range(1, 21))["p95_ms"] == 19.0  # nearest rank of 20 observations
    assert timing.summarise_ms([])["median_ms"] is None


def test_summary_rejects_impossible_values():
    with pytest.raises(timing.InstrumentationError):
        timing.summarise_ms([5.0, -1.0])


# --- the agent's receipt ----------------------------------------------------------------------

class ScriptedLLM:
    model = "fake-model"

    def __init__(self, items):
        self.items = list(items)

    def generate(self, *, system, prompt, schema, purpose=""):
        item = self.items.pop(0)
        cached = isinstance(item, tuple)
        value = item[0] if cached else item
        call = (LLMCall(model="fake-model", sdk_version="t", source="cache", latency_ms=None, attempts=0) if cached
                else LLMCall(model="fake-model", sdk_version="t", latency_ms=15.0, attempts=1))
        return LLMResult(value=value, call=call)


def route(action, **fields):
    values = dict(portfolio="", sql_question="", sector="", clarification_question="", options=[],
                  decline_kind="none", reason="Scripted for the test.")
    values.update(fields)
    return RouterDecision(action=action, **values)


def draft(sql):
    return SqlDraft(answerable=True, decline_reason="none", sql=sql, explanation="Scripted query.")


@pytest.fixture
def make(readonly_db):
    context = SemanticContext.from_db(readonly_db)
    return lambda *items: PortfolioAgent(readonly_db, context, ScriptedLLM(items), fingerprint="c" * 64)


def test_sql_route_measures_planner_generation_execution_and_total(make):
    t = make(route("sql"), draft("SELECT COUNT(*) AS n FROM portfolios")).ask("How many portfolios?").timings
    assert t.latency_schema == 2
    assert t.planner_latency_ms == 15.0 and t.sql_generation_latency_ms == 15.0
    assert t.sql_execution_latency_ms is not None and t.sql_execution_latency_ms >= 0
    assert t.exposure_calculation_latency_ms is None  # no exposure ran: null, not 0
    assert t.total_latency_ms is not None and t.orchestration_latency_ms is not None


def test_stages_that_did_not_run_are_null_on_every_route(make):
    declined = make(route("decline", decline_kind="out_of_scope")).ask("What is the weather?").timings
    assert declined.planner_latency_ms == 15.0
    assert (declined.sql_generation_latency_ms, declined.sql_execution_latency_ms,
            declined.exposure_calculation_latency_ms) == (None, None, None)
    exposure = make(route("exposure", portfolio="Tech Innovation Fund")).ask("Sector exposure of Tech Innovation Fund").timings
    assert exposure.exposure_calculation_latency_ms is not None
    assert (exposure.sql_generation_latency_ms, exposure.sql_execution_latency_ms) == (None, None)


def test_a_cached_planner_response_has_no_planner_latency(make):
    receipt = make((route("decline", decline_kind="out_of_scope"),)).ask("What is the weather?")
    assert receipt.timings.planner_latency_ms is None  # nothing was sent, so nothing was measured
    assert receipt.timings.total_latency_ms is not None and receipt.model.llm_latency_ms is None


def test_invalid_input_still_measures_its_total_and_nothing_else(make):
    receipt = make().ask("")
    assert receipt.status == "invalid_input" and receipt.timings.total_latency_ms is not None
    assert receipt.timings.planner_latency_ms is None


def test_total_is_measured_on_its_own_clock_and_is_not_the_sum_of_the_stages(make, monkeypatch):
    # Clock readings, in ns: total starts (0); the exposure calculation starts (10 ms) and ends (12 ms);
    # the receipt is built (40 ms). Planner (scripted Gemini request): 15 ms.
    readings = itertools.chain([0, 10_000_000, 12_000_000, 40_000_000])
    monkeypatch.setattr(timing, "now_ns", lambda: next(readings))  # a fifth reading would raise StopIteration
    t = make(route("exposure", portfolio="Tech Innovation Fund")).ask("Sector exposure of Tech Innovation Fund").timings
    assert t.total_latency_ms == 40.0
    assert t.exposure_calculation_latency_ms == 2.0 and t.planner_latency_ms == 15.0
    assert t.total_latency_ms != t.planner_latency_ms + t.exposure_calculation_latency_ms
    assert t.orchestration_latency_ms == 40.0 - 15.0 - 2.0  # what is left once each nested interval is removed once


def test_sql_stages_are_not_added_to_the_sql_step_twice(make):
    receipt = make(route("sql"), draft("SELECT COUNT(*) AS n FROM portfolios")).ask("How many portfolios?")
    sql, t = receipt.sql, receipt.timings
    # generation and execution lie inside the SQL tool's own total; orchestration subtracts that total once
    assert sql.total_latency_ms >= sql.sql_latency_ms
    expected = max(0.0, t.total_latency_ms - t.planner_latency_ms - sql.total_latency_ms)
    assert t.orchestration_latency_ms == pytest.approx(expected, abs=0.01)


# --- historical records -----------------------------------------------------------------------

LEGACY = {"total_ms": 1234.5, "llm_ms": 1000.0, "sql_ms": 3.25, "exposure_ms": 0.0}


def test_a_legacy_timings_record_is_read_exactly_once_and_never_rescaled():
    t = Timings.model_validate(LEGACY)
    assert t.latency_schema == 1
    assert (t.total_latency_ms, t.legacy_llm_ms, t.sql_execution_latency_ms) == (1234.5, 1000.0, 3.25)
    assert t.llm_latency_ms == 1000.0  # not 1,000,000 and not 1
    assert t.planner_latency_ms is None  # the legacy figure cannot be split, so it is not invented


def test_recorded_receipts_load_with_their_original_values():
    path = next((PROJECT_ROOT / "reports" / "runs").glob("agent-*/agent_outputs.jsonl"), None)
    if path is None:
        pytest.skip("no recorded agent run")
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)["receipt"]["timings"]
        loaded = FinancialAnswerReceipt.model_validate(json.loads(line)["receipt"]).timings
        if "total_ms" in raw:
            assert loaded.total_latency_ms == raw["total_ms"] and loaded.latency_schema == 1
        else:
            assert loaded.total_latency_ms == raw["total_latency_ms"]


# --- no simulated latency, consistent units ----------------------------------------------------

def test_production_code_contains_no_hardcoded_or_wall_clock_latency():
    hardcoded = re.compile(r"latency\w*\s*(:\s*float\s*)?=\s*[0-9]")
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not hardcoded.search(source), f"{path.name} assigns a literal latency"
        # Wall-clock calls exist only to stamp records (created_utc, ledger lines), never to time anything.
        assert "time.time()" not in source, f"{path.name} derives a duration from the wall clock"


def test_every_latency_field_is_named_in_milliseconds():
    for model in (Timings, ModelUsage, SqlToolResult, LLMCall):
        names = [n for n in model.model_fields if ("latency" in n and n != "latency_schema") or n == "wait_ms"]
        assert names and all(n.endswith("_ms") for n in names), (model.__name__, names)


def test_cli_reports_latency_in_milliseconds(make, monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", lambda: make(route("sql"), draft("SELECT COUNT(*) AS n FROM portfolios")))
    assert cli.main(["ask", "How many portfolios do we have?"]) == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if l.strip().startswith("Latency:"))
    assert re.search(r"Total application [\d,.]+ ms", line) and "Gemini request: routing 15.00 ms" in line
    assert "?" not in line and "; Gemini request: SQL 15.00 ms" in line  # an ASCII separator survives the CLI's text filter
    assert "Exposure calculation" not in line  # not run, so not shown
    assert " s " not in line and "second" not in line
