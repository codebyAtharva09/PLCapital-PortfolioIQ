"""Milestone 1: settings come from the environment, and secrets never leak.

Assertions compare booleans, never secret values, so a failure cannot print a secret.
"""

import re

from portfolioiq import config
from portfolioiq.config import DEFAULT_GEMINI_MODEL, PLACEHOLDER_API_KEYS, PROJECT_ROOT

# Shapes of real Google credentials: classic API keys and the newer "AQ." format.
CREDENTIAL_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"AQ\.[0-9A-Za-z_\-]{20,}"),
]


def _ignore_dotenv(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)


def test_model_defaults_to_pinned_id_and_env_var_overrides_it(monkeypatch):
    _ignore_dotenv(monkeypatch)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert config.load_settings().gemini_model == DEFAULT_GEMINI_MODEL

    monkeypatch.setenv("GEMINI_MODEL", "gemini-some-other-model")
    assert config.load_settings().gemini_model == "gemini-some-other-model"


def test_key_is_read_from_the_environment(monkeypatch):
    _ignore_dotenv(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-from-environment")
    assert config.load_settings().gemini_api_key == "fake-key-from-environment"


def test_placeholder_key_counts_as_missing(monkeypatch):
    _ignore_dotenv(monkeypatch)
    for placeholder in PLACEHOLDER_API_KEYS:
        monkeypatch.setenv("GEMINI_API_KEY", placeholder)
        assert config.load_settings().gemini_api_key is None


def test_settings_repr_never_shows_the_key(monkeypatch):
    _ignore_dotenv(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-secret-value-123")
    shown = "fake-secret-value-123" in repr(config.load_settings())
    assert not shown, "Settings repr must not include the API key"


def test_env_example_contains_only_placeholders():
    # .env.example is committed to git; a real key belongs only in .env (git-ignored).
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    key_lines = [line for line in text.splitlines() if line.startswith("GEMINI_API_KEY=")]
    assert len(key_lines) == 1, ".env.example must define GEMINI_API_KEY exactly once"

    is_placeholder = key_lines[0].split("=", 1)[1].strip() in PLACEHOLDER_API_KEYS
    assert is_placeholder, ".env.example must contain a placeholder, not a real key"

    looks_like_credential = any(pattern.search(text) for pattern in CREDENTIAL_PATTERNS)
    assert not looks_like_credential, ".env.example contains something shaped like a real key"
