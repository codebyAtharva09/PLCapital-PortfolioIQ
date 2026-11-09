"""DEMO / FIXTURE MODE: recorded Gemini responses replayed offline; nothing is ever sent to the API."""

import dataclasses
import json

import pytest

import portfolioiq.agent as agent_module
from portfolioiq.agent import PortfolioAgent
from portfolioiq.cli import main
from portfolioiq.config import PROJECT_ROOT, load_settings
from portfolioiq.llm import LLMError, request_key
from portfolioiq.replay import DEMO_LABEL, RecordedLLM, is_replay, load_recorded
from portfolioiq.sql_tool import SqlDraft

LEDGER = PROJECT_ROOT / "reports" / "api_usage.jsonl"
A1 = "How many holdings does the Dividend Aristocrats Fund have?"
A4 = "Which high-risk portfolio has the largest Technology exposure?"


def entry(key, raw, status="ok", model="gemini-3.6-flash", when="2026-11-07T12:00:00+00:00"):
    return {"timestamp_utc": when, "event": "request", "api_request": True, "purpose": "test", "model": model,
            "model_version": "gemini-3.6-flash", "status": status, "request_hash": key[:16], "raw_response": raw}


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(load_settings(), gemini_model="gemini-3.6-flash", gemini_thinking_level="LOW",
                               api_usage_log=tmp_path / "ledger.jsonl")


def write(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


DRAFT = SqlDraft(answerable=True, decline_reason="none", sql="SELECT COUNT(*) FROM portfolios", explanation="Count.")


def test_recorded_response_is_replayed_and_labelled(settings):
    key = request_key("gemini-3.6-flash", "LOW", "system", "prompt", SqlDraft)
    write(settings.api_usage_log, [entry(key, DRAFT.model_dump_json())])
    llm = RecordedLLM(settings)
    result = llm.generate(system="system", prompt="prompt", schema=SqlDraft)
    assert result.value == DRAFT and is_replay(llm)
    call = result.call
    assert (call.source, call.attempts, call.latency_ms, call.cached_at) == ("replay", 0, None, "2026-11-07T12:00:00+00:00")
    assert call.request_hash == key


def test_unrecorded_request_fails_without_inventing_anything(settings):
    write(settings.api_usage_log, [])
    with pytest.raises(LLMError) as raised:
        RecordedLLM(settings).generate(system="system", prompt="other", schema=SqlDraft)
    assert raised.value.kind == "not_recorded" and DEMO_LABEL in raised.value.message
    assert raised.value.attempts == 0


def test_only_successful_responses_for_the_configured_model_are_used(settings):
    key = request_key("gemini-3.6-flash", "LOW", "s", "p", SqlDraft)
    write(settings.api_usage_log, [entry(key, DRAFT.model_dump_json(), status="503 UNAVAILABLE"),
                                   entry(key, DRAFT.model_dump_json(), model="gemini-3.5-flash-lite"),
                                   {"event": "cache_hit", "request_hash": key[:16]}, "not json"])
    assert load_recorded(settings.api_usage_log, "gemini-3.6-flash") == {}


def test_malformed_recorded_response_is_not_used(settings):
    key = request_key("gemini-3.6-flash", "LOW", "s", "p", SqlDraft)
    write(settings.api_usage_log, [entry(key, '{"unexpected": true}')])
    with pytest.raises(LLMError) as raised:
        RecordedLLM(settings).generate(system="s", prompt="p", schema=SqlDraft)
    assert raised.value.kind == "malformed_output"


def test_recorded_agent_questions_replay_offline_without_touching_the_ledger(monkeypatch):
    def no_live_client(*args, **kwargs):
        raise AssertionError("the live Gemini client must not be created in demo mode")

    monkeypatch.setattr(agent_module, "GeminiLLM", no_live_client)
    before = LEDGER.read_bytes()
    settings = load_settings()
    agent = PortfolioAgent.from_settings(settings, llm=RecordedLLM(settings))
    hybrid = agent.ask(A4)
    assert (hybrid.status, hybrid.route) == ("answered", "exposure_for_sql_results")
    assert hybrid.comparison.leaders_method_a == ["Tech Innovation Fund"]
    assert (hybrid.model.api_requests, hybrid.model.cache_hits, hybrid.model.replayed_responses) == (0, 0, 2)
    count = agent.ask(A1)
    assert count.status == "answered" and count.sql.execution.rows == [[4]]
    assert all(c.source == "replay" for step in count.tool_calls for c in step.llm_calls)
    missing = agent.ask("What is the weather in Mumbai?")
    assert (missing.status, missing.error_kind, missing.model.api_requests) == ("error", "not_recorded", 0)
    assert LEDGER.read_bytes() == before  # replay never writes to the usage ledger


def test_cli_replay_is_labelled_and_exit_codes_are_honest(capsys):
    assert main(["ask", "--replay", A1]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith(f"[{DEMO_LABEL}] Gemini output replayed") and "replayed 2" in printed
    assert main(["ask", "--replay", "What is the weather in Mumbai?"]) == 4
    assert "No recorded Gemini response matches this question" in capsys.readouterr().out
    assert main(["sql", "--replay", "List each bond with its coupon rate and maturity date."]) == 0
    assert capsys.readouterr().out.startswith(f"[{DEMO_LABEL}]")
