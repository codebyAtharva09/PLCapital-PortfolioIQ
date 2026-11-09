"""Milestone 1: the connectivity check reports clearly and never leaks the API key.

These tests use a fake client, so they need no network and no real key.
"""

from pathlib import Path
from types import SimpleNamespace

from google.genai import errors

from portfolioiq import gemini_check
from portfolioiq.config import Settings

FAKE_KEY = "fake-key-that-must-never-be-printed"


def _use_settings(monkeypatch, key):
    settings = Settings(
        gemini_api_key=key,
        gemini_model="gemini-test-model",
        data_dir=Path("."),
        schema_path=Path("schema.sql"),
    )
    monkeypatch.setattr(gemini_check, "load_settings", lambda: settings)


def _use_client(monkeypatch, generate_content):
    class FakeClient:
        def __init__(self, **kwargs):
            self.models = SimpleNamespace(generate_content=generate_content)

    monkeypatch.setattr(gemini_check.genai, "Client", FakeClient)


def test_missing_key_exits_2_without_calling_the_api(monkeypatch, capsys):
    _use_settings(monkeypatch, key=None)

    def must_not_be_called(**kwargs):
        raise AssertionError("the API must not be called without a key")

    _use_client(monkeypatch, must_not_be_called)
    assert gemini_check.check() == 2
    assert ".env.example" in capsys.readouterr().err


def test_api_error_is_one_readable_line_without_the_key(monkeypatch, capsys):
    _use_settings(monkeypatch, key=FAKE_KEY)

    def reject(**kwargs):
        body = {
            "error": {
                "code": 400,
                "message": f"API key not valid ({FAKE_KEY})",
                "status": "INVALID_ARGUMENT",
            }
        }
        raise errors.ClientError(400, body)

    _use_client(monkeypatch, reject)
    assert gemini_check.check() == 1
    err = capsys.readouterr().err
    assert "400" in err and "INVALID_ARGUMENT" in err
    assert FAKE_KEY not in err
    assert len(err.strip().splitlines()) == 1


def test_network_failure_does_not_leak_the_key(monkeypatch, capsys):
    _use_settings(monkeypatch, key=FAKE_KEY)

    def fail(**kwargs):
        raise ConnectionError(f"could not connect using key={FAKE_KEY}")

    _use_client(monkeypatch, fail)
    assert gemini_check.check() == 1
    err = capsys.readouterr().err
    assert FAKE_KEY not in err and "***" in err


def test_success_reports_model_latency_and_tokens(monkeypatch, capsys):
    _use_settings(monkeypatch, key=FAKE_KEY)
    usage = SimpleNamespace(
        prompt_token_count=8, candidates_token_count=1, thoughts_token_count=0, total_token_count=9
    )

    def succeed(**kwargs):
        assert kwargs["model"] == "gemini-test-model"
        return SimpleNamespace(text="OK", usage_metadata=usage, model_version="gemini-test-model")

    _use_client(monkeypatch, succeed)
    assert gemini_check.check() == 0
    out = capsys.readouterr().out
    assert "requested model: gemini-test-model" in out
    assert "latency:" in out
    assert "total=9" in out


def test_model_can_be_overridden(monkeypatch):
    _use_settings(monkeypatch, key=FAKE_KEY)
    seen = {}

    def record(**kwargs):
        seen["model"] = kwargs["model"]
        return SimpleNamespace(text="OK", usage_metadata=None, model_version=None)

    _use_client(monkeypatch, record)
    assert gemini_check.check(model="gemini-other-model") == 0
    assert seen["model"] == "gemini-other-model"
