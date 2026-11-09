"""Gemini wrapper: strict request budget, safe failures, ledger and cache (fake client, no network)."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from google import genai
from google.genai import errors
from pydantic import BaseModel

from portfolioiq.config import Settings
from portfolioiq.llm import GeminiLLM, LLMError

FAKE_KEY = "fake-key-that-must-never-be-printed"
DAILY_QUOTA_MESSAGE = ("You exceeded your current quota, please check your plan and billing details.\n"
                       "* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
                       "limit: 20, model: gemini-3.6-flash, description: Generate content requests per day limit exceeded "
                       "on Free Tier.\nPlease retry in 15h8m31.922953188s.")


class Answer(BaseModel):
    value: str


def settings(**overrides):
    values = dict(gemini_api_key=FAKE_KEY, gemini_model="gemini-3.6-flash", data_dir=Path("."),
                  schema_path=Path("schema.sql"))
    values.update(overrides)
    return Settings(**values)


def response(value="ok", parsed=True, text=None, blocked=False):
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=3, thoughts_token_count=5,
                            total_token_count=18)
    return SimpleNamespace(
        parsed=Answer(value=value) if parsed else None,
        text=text if text is not None else (None if blocked else f'{{"value": "{value}"}}'),
        usage_metadata=usage, model_version="gemini-3.6-flash-001",
        prompt_feedback=SimpleNamespace(block_reason="SAFETY") if blocked else None,
    )


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make(outcomes, **overrides):
    client, clock = FakeClient(outcomes), Clock()
    llm = GeminiLLM(settings(**overrides), client=client, sleep=clock.sleep, clock=clock)
    return llm, client, clock


def server_error(code=503):
    return errors.ServerError(code, {"error": {"code": code, "message": "high demand", "status": "UNAVAILABLE"}})


def rate_limited(message="Resource exhausted.", retry_delay=None):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}] if retry_delay else []
    return errors.ClientError(429, {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED",
                                              "details": details}})


def client_error(code, status, message="bad request"):
    return errors.ClientError(code, {"error": {"code": code, "message": message, "status": status}})


def ask(llm, prompt="prompt"):
    return llm.generate(system="system", prompt=prompt, schema=Answer, purpose="test")


def ledger_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- basics -----------------------------------------------------------------------------------

def test_missing_key_fails_before_any_network_call():
    with pytest.raises(LLMError) as raised:
        GeminiLLM(settings(gemini_api_key=None), client=FakeClient([]))
    assert raised.value.kind == "not_configured" and "GEMINI_API_KEY" in raised.value.message


def test_success_returns_the_parsed_value_and_usage():
    llm, client, _ = make([response("hello")])
    result = ask(llm)
    assert result.value.value == "hello"
    call = result.call
    assert (call.model, call.model_version, call.attempts, call.source) == (
        "gemini-3.6-flash", "gemini-3.6-flash-001", 1, "api")
    assert (call.prompt_tokens, call.output_tokens, call.thinking_tokens, call.total_tokens) == (10, 3, 5, 18)
    assert call.sdk_version == genai.__version__
    request = client.calls[0]
    assert request["model"] == "gemini-3.6-flash"
    assert request["config"].response_schema is Answer
    assert request["config"].thinking_config.thinking_level == "LOW"


def test_thinking_level_can_be_left_to_the_model():
    llm, client, _ = make([response()], gemini_thinking_level=None)
    ask(llm)
    assert client.calls[0]["config"].thinking_config is None


def test_text_is_parsed_when_the_sdk_did_not_parse():
    llm, _, _ = make([response(parsed=False, text='{"value": "from text"}')])
    assert ask(llm).value.value == "from text"


def test_malformed_output_is_an_error_not_a_guess():
    llm, _, _ = make([response(parsed=False, text="SELECT * FROM holdings")])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "malformed_output"


def test_blocked_response_is_reported():
    llm, _, _ = make([response(parsed=False, blocked=True)])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "blocked"


# --- retry policy -----------------------------------------------------------------------------

def test_server_error_is_retried_once():
    llm, client, clock = make([server_error(503), response()])
    result = ask(llm)
    assert result.call.attempts == 2 and len(client.calls) == 2
    assert len(clock.sleeps) == 1 and 5.0 <= clock.sleeps[0] <= 6.0 and result.call.wait_ms == clock.sleeps[0] * 1000  # base wait plus up to 1 s of jitter


def test_server_errors_are_not_retried_repeatedly():
    llm, client, _ = make([server_error(), server_error()])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "unavailable" and len(client.calls) == 2


def test_per_minute_rate_limit_waits_googles_delay_then_retries_once():
    llm, client, clock = make([rate_limited(retry_delay="20s"), response()])
    assert ask(llm).value.value == "ok"
    assert clock.sleeps == [21.0] and len(client.calls) == 2


def test_rate_limit_without_a_delay_is_not_retried():
    llm, client, _ = make([rate_limited()])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "rate_limited" and len(client.calls) == 1


def test_daily_quota_is_never_retried():
    llm, client, clock = make([rate_limited(DAILY_QUOTA_MESSAGE)])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "quota_exhausted"
    assert raised.value.retry_after_s == pytest.approx(15 * 3600 + 8 * 60 + 31.922953188)
    assert len(client.calls) == 1 and clock.sleeps == []


def test_long_retry_delay_counts_as_exhausted_quota():
    llm, client, _ = make([rate_limited(retry_delay="3600s")])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "quota_exhausted" and len(client.calls) == 1


def test_client_errors_are_not_retried():
    llm, client, _ = make([client_error(400, "INVALID_ARGUMENT")])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "api_error" and len(client.calls) == 1


def test_network_failure_is_retried_once_then_reported():
    llm, client, _ = make([httpx.ConnectError("connection refused")] * 2)
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.kind == "network" and len(client.calls) == 2


@pytest.mark.parametrize("outcomes, sent", [
    ([server_error(), server_error()], 2),
    ([rate_limited(DAILY_QUOTA_MESSAGE)], 1),
    ([response(parsed=False, text="not json")], 1),
])
def test_errors_report_how_many_requests_were_sent(outcomes, sent):
    llm, _, _ = make(outcomes)
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert raised.value.attempts == sent  # a failed request is never assumed to be free


def test_attempts_are_capped_by_settings():
    llm, client, _ = make([server_error()], llm_max_attempts=1)
    with pytest.raises(LLMError):
        ask(llm)
    assert len(client.calls) == 1


def test_error_messages_never_contain_the_key():
    llm, _, _ = make([client_error(400, "INVALID_ARGUMENT", f"API key {FAKE_KEY} not valid")])
    with pytest.raises(LLMError) as raised:
        ask(llm)
    leaked = FAKE_KEY in str(raised.value) or FAKE_KEY in raised.value.message
    assert not leaked and "***" in raised.value.message


def test_pacing_spaces_consecutive_requests():
    llm, _, clock = make([response(), response()], llm_min_interval_s=13.0)
    ask(llm, "first")
    ask(llm, "second")
    assert clock.sleeps == [pytest.approx(13.0)]


def test_only_the_configured_model_is_ever_called():
    llm, client, _ = make([server_error(), response()])
    ask(llm)
    assert {call["model"] for call in client.calls} == {"gemini-3.6-flash"}


# --- usage ledger -----------------------------------------------------------------------------

def test_ledger_records_every_request_and_never_the_key(tmp_path):
    ledger = tmp_path / "api_usage.jsonl"
    llm, _, _ = make([server_error(), response("saved")], api_usage_log=ledger)
    ask(llm)
    lines = ledger_lines(ledger)
    assert [line["status"] for line in lines] == ["503 UNAVAILABLE", "ok"]
    assert all(line["api_request"] and line["model"] == "gemini-3.6-flash" and line["purpose"] == "test" for line in lines)
    assert lines[1]["raw_response"] == '{"value": "saved"}' and lines[1]["total_tokens"] == 18
    assert lines[0]["error_kind"] == "unavailable" and "raw_response" not in lines[0]
    assert FAKE_KEY not in ledger.read_text(encoding="utf-8")


def test_ledger_records_quota_exhaustion(tmp_path):
    ledger = tmp_path / "api_usage.jsonl"
    llm, _, _ = make([rate_limited(DAILY_QUOTA_MESSAGE)], api_usage_log=ledger)
    with pytest.raises(LLMError):
        ask(llm)
    (line,) = ledger_lines(ledger)
    assert line["status"] == "429 RESOURCE_EXHAUSTED" and line["error_kind"] == "quota_exhausted"
    assert line["retry_after_s"] > 15 * 3600


# --- response cache ---------------------------------------------------------------------------

def test_identical_request_is_answered_from_the_cache(tmp_path):
    ledger = tmp_path / "api_usage.jsonl"
    llm, client, _ = make([response("first answer")], llm_cache_dir=tmp_path / "cache", api_usage_log=ledger)
    first = ask(llm, "same prompt")
    second = ask(llm, "same prompt")
    assert len(client.calls) == 1  # the second answer cost no request
    assert second.value.value == "first answer"
    assert (second.call.source, second.call.attempts, second.call.latency_ms) == ("cache", 0, None)  # no request: nothing measured
    assert second.call.cached_at and second.call.request_hash == first.call.request_hash
    assert [line["event"] for line in ledger_lines(ledger)] == ["request", "cache_hit"]


def test_different_request_is_not_answered_from_the_cache(tmp_path):
    llm, client, _ = make([response("a"), response("b")], llm_cache_dir=tmp_path / "cache")
    assert ask(llm, "prompt one").value.value == "a"
    assert ask(llm, "prompt two").value.value == "b"
    assert len(client.calls) == 2


def test_corrupt_cache_entry_is_ignored(tmp_path):
    cache = tmp_path / "cache"
    llm, client, _ = make([response("fresh"), response("again")], llm_cache_dir=cache)
    first = ask(llm, "prompt")
    (cache / f"{first.call.request_hash}.json").write_text("not json", encoding="utf-8")
    assert ask(llm, "prompt").value.value == "again" and len(client.calls) == 2


def test_failures_are_never_cached(tmp_path):
    cache = tmp_path / "cache"
    llm, client, _ = make([client_error(400, "INVALID_ARGUMENT"), response("ok")], llm_cache_dir=cache)
    with pytest.raises(LLMError):
        ask(llm, "prompt")
    assert not list(cache.glob("*.json")) if cache.exists() else True
    assert ask(llm, "prompt").value.value == "ok" and len(client.calls) == 2


# --- hard request cap -------------------------------------------------------------------------

def test_request_cap_is_never_exceeded_and_the_stop_is_recorded(tmp_path):
    ledger = tmp_path / "api_usage.jsonl"
    llm, client, _ = make([response("first"), response("never sent")], llm_request_cap=1, api_usage_log=ledger)
    assert ask(llm, "first").value.value == "first"
    with pytest.raises(LLMError) as raised:
        ask(llm, "second")
    assert (raised.value.kind, raised.value.attempts) == ("budget_exhausted", 0)
    assert len(client.calls) == 1 and llm.requests_sent == 1
    assert [(line["event"], line["api_request"]) for line in ledger_lines(ledger)] == [
        ("request", True), ("budget_exhausted", False)]


def test_retries_and_failed_requests_count_against_the_cap():
    llm, client, _ = make([server_error(), response("never sent")], llm_request_cap=1)
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert (raised.value.kind, raised.value.attempts) == ("budget_exhausted", 1)
    assert "retry after the unavailable error was not sent" in raised.value.message and len(client.calls) == 1


def test_cache_hits_do_not_count_against_the_cap(tmp_path):
    llm, client, _ = make([response("saved")], llm_request_cap=1, llm_cache_dir=tmp_path / "cache")
    ask(llm, "same prompt")
    assert ask(llm, "same prompt").call.source == "cache" and llm.requests_sent == 1 and len(client.calls) == 1


def test_raw_text_of_a_malformed_response_is_kept_in_the_ledger(tmp_path):
    ledger = tmp_path / "api_usage.jsonl"
    llm, _, _ = make([response(parsed=False, text="not json")], api_usage_log=ledger)
    with pytest.raises(LLMError):
        ask(llm)
    (line,) = ledger_lines(ledger)
    assert (line["status"], line["raw_response"]) == ("malformed_output", "not json")
